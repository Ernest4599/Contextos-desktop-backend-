"""
AIOS memory engine: Input -> Understand -> Extract -> Classify -> Store -> Retrieve.

Deliberately simple per the MVP spec: one LLM call extracts discrete
memory items from free text, classifies each into a fixed category set,
and decides against the user's existing memories whether each item is
new, a duplicate (touch existing), a clean update (old state ->
historical, new state -> active), or a genuine conflict (held as
needs_review rather than merged - see CLASSIFY_SYSTEM_PROMPT). No
separate embedding/similarity system - the LLM does this classification
directly, same pattern as context_extractor.py and quick_prompt.py.

Every UI surface (Overview summary, Identity Strength, Recent Memories,
category pages, AIOS Quick Prompt) reads from this same engine - no
separate backend logic per view.

Memory content is encrypted at rest (see services/encryption.py) -
every write encrypts before storing, every read decrypts immediately
after fetching, including content fed back into LLM prompts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from services.llm_providers import LLMProviderError, call_llm, parse_llm_json
from services.models import AiosMemory
from services.encryption import encrypt_text, decrypt_text

AiosError = LLMProviderError

ALLOWED_CATEGORIES = [
    "personality", "preference", "goal", "interest",
    "knowledge", "writing_style", "important_fact", "context",
]
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_TEMPORAL_STATES = {"permanent", "current", "temporary", "historical", "unknown"}

MAX_EXISTING_MEMORIES_IN_PROMPT = 60
MAX_QUICK_PROMPT_LENGTH = 2000
MAX_TELL_INPUT_LENGTH = 4000

# Categories checked for the Identity Strength completeness score.
IDENTITY_STRENGTH_CATEGORIES = [
    "personality", "writing_style", "preference", "goal", "knowledge", "important_fact",
]
MIN_MEMORIES_FOR_STRENGTH_CHECK = 10

# Quick Prompt edit-feedback loop (algorithm steps 45-46). A fixed,
# small vocabulary - never freeform LLM text - so repeat-counting in
# AiosEditPattern is exact string matching, not fuzzy. "none" means the
# edit doesn't fit any tracked pattern (e.g. a typo fix) and is ignored.
EDIT_PATTERN_KEYS = [
    "shortens_output", "lengthens_output", "more_casual_tone", "more_formal_tone",
    "removes_role_framing", "adds_specificity", "removes_generic_phrasing", "changes_structure",
    "none",
]

# How many separate Quick Prompt sessions must show the same edit
# pattern before AIOS records it as a (low-confidence) memory. A single
# edit is never enough - see the "repeated behavior, not one edit" rule.
EDIT_PATTERN_REPEAT_THRESHOLD = 3

EDIT_PATTERN_DESCRIPTIONS = {
    "shortens_output": "tends to shorten generated prompts",
    "lengthens_output": "tends to add more detail to generated prompts",
    "more_casual_tone": "tends to make generated prompts more casual",
    "more_formal_tone": "tends to make generated prompts more formal",
    "removes_role_framing": "tends to remove role/persona framing from generated prompts",
    "adds_specificity": "tends to add more specific detail when editing generated prompts",
    "removes_generic_phrasing": "tends to remove generic filler phrasing from generated prompts",
    "changes_structure": "tends to restructure the format of generated prompts",
}

EDIT_CLASSIFY_SYSTEM_PROMPT = f"""You compare an AI-generated prompt to the user's edited version and classify the SINGLE most salient pattern in what changed, from this fixed list only:

{chr(10).join(f"- {k}: {v}" for k, v in EDIT_PATTERN_DESCRIPTIONS.items())}
- none: the edit doesn't clearly fit any of the above (e.g. a typo fix, a factual correction, an insignificant tweak, or multiple unrelated changes with no single dominant pattern)

Respond with ONLY a JSON object: {{"pattern_key": "..."}} using exactly one of the keys above (including "none"). No preamble, no markdown fences, no extra commentary."""

CLASSIFY_SYSTEM_PROMPT = """You are AIOS, an identity layer that learns what matters about a user from what they tell you, to later personalize AI prompts on their behalf.

Given new input from the user and a list of their existing stored memories, do the following:

1. Extract each discrete, useful piece of information from the input as a separate item. A single message can contain multiple items (e.g. a preference AND a goal). Discard filler that carries no lasting information about the user (greetings, small talk). Do not turn a passing mention into a fact about the user - only extract things that are actually about them.
2. For each item, classify it into exactly one category from this fixed list: personality, preference, goal, interest, knowledge, writing_style, important_fact, context.
3. For each item, decide an action by comparing it to the existing memories provided:
   - "new" - genuinely new information, no matching existing memory
   - "duplicate" - restates an existing memory with the same meaning (include the matching existing memory's id)
   - "update" - clearly supersedes an existing memory because the user's situation changed over time (e.g. old: "works alone on ContextOS", new: "hired two developers") - include the existing memory's id
   - "conflict" - contradicts an existing memory but it's genuinely unclear whether this is a correction, a change over time, or a mistake - include the existing memory's id; do NOT guess, flag it instead
4. For each item, set "confidence":
   - "high" - the user explicitly stated this
   - "medium" - a reasonable interpretation of what they said, not stated word-for-word
   - "low" - your own inference that goes beyond what they actually said
   Never silently upgrade a low-confidence inference into a high-confidence fact.
5. For each item, set "temporal_state":
   - "permanent" - an enduring identity fact (e.g. occupation, name)
   - "current" - true right now but expected to change (e.g. currently building X)
   - "temporary" - explicitly short-term (e.g. this week, this month)
   - "historical" - about the past, no longer current
   - "unknown" - can't tell from what was said

Respond with ONLY a JSON object of this exact shape:
{"items": [{"content": "...", "category": "...", "action": "new", "confidence": "high", "temporal_state": "permanent"}, {"content": "...", "category": "...", "action": "update", "existing_id": 12, "confidence": "high", "temporal_state": "current"}]}

If nothing useful is present, return {"items": []}. No preamble, no markdown fences."""

AIOS_QUICK_PROMPT_SYSTEM_PROMPT = """You are AIOS's Quick Prompt engine. You build a single, complete, ready-to-use prompt personalized to this specific user, for them to paste into any AI (ChatGPT, Claude, Gemini, or any other). You do NOT execute the request yourself - you only construct the prompt.

You are given:
1. USER REQUEST - what they want help creating
2. USER IDENTITY - memories AIOS has learned about this user (personality, preferences, goals, writing style, knowledge, etc). This may say AIOS doesn't know much yet.

Process:
1. Understand what the user is asking for.
2. From USER IDENTITY, use only what is genuinely relevant to this specific request - ignore anything that doesn't help here. Never invent identity details that are not present.
3. Choose the most appropriate expert role for the request.
4. Build one complete prompt that: states the role, naturally incorporates relevant personalization (tone, preferences, constraints, context) only where it improves the result, states the objective clearly, specifies the expected output format, and is ready to paste directly into another AI with no further editing needed.
5. If USER IDENTITY has nothing relevant, build a strong prompt without fabricating personalization.

Respond with ONLY a JSON object with this exact key:
{"prompt": "..."}

No preamble, no markdown fences, no extra commentary."""


def _format_existing_memories(memories: List[AiosMemory]) -> str:
    if not memories:
        return "(none yet)"
    lines = [f'id={m.id} category={m.category} content="{decrypt_text(m.content)}"' for m in memories]
    return "\n".join(lines)


def tell_aios(db: Session, user_id: int, raw_input: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise AiosError("Tell AIOS something first")
    if len(raw_input) > MAX_TELL_INPUT_LENGTH:
        raise AiosError(f"That's too long — please keep it under {MAX_TELL_INPUT_LENGTH} characters")

    batch_id = str(uuid.uuid4())

    existing = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(MAX_EXISTING_MEMORIES_IN_PROMPT)
        .all()
    )

    user_content = (
        f"NEW INPUT:\n{raw_input}\n\n"
        f"EXISTING MEMORIES:\n{_format_existing_memories(existing)}"
    )

    raw = call_llm(CLASSIFY_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)
    parsed = parse_llm_json(raw)
    items = parsed.get("items", [])
    if not isinstance(items, list):
        items = []

    existing_by_id = {m.id: m for m in existing}
    added: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    skipped_duplicates = 0
    now = datetime.now(timezone.utc)

    for item in items:
        content = (item.get("content") or "").strip()
        category = item.get("category")
        action = item.get("action", "new")
        confidence = item.get("confidence") if item.get("confidence") in ALLOWED_CONFIDENCE else "medium"
        temporal_state = item.get("temporal_state") if item.get("temporal_state") in ALLOWED_TEMPORAL_STATES else "unknown"

        if not content or category not in ALLOWED_CATEGORIES:
            continue

        if action == "duplicate":
            existing_id = item.get("existing_id")
            match = existing_by_id.get(existing_id)
            if match:
                match.last_confirmed_at = now
                match.updated_at = now
                match.batch_id = batch_id
                db.add(match)
            skipped_duplicates += 1
            continue

        if action == "update":
            existing_id = item.get("existing_id")
            match = existing_by_id.get(existing_id)
            if match:
                match.status = "outdated"
                match.temporal_state = "historical"
                db.add(match)
            new_memory = AiosMemory(
                user_id=user_id, content=encrypt_text(content), category=category,
                source="user_input", confidence=confidence, temporal_state=temporal_state,
                status="active", batch_id=batch_id,
            )
            db.add(new_memory)
            updated.append({"content": content, "category": category})
            continue

        if action == "conflict":
            existing_id = item.get("existing_id")
            match = existing_by_id.get(existing_id)
            if match:
                match.status = "needs_review"
                db.add(match)
            new_memory = AiosMemory(
                user_id=user_id, content=encrypt_text(content), category=category,
                source="user_input", confidence=confidence, temporal_state=temporal_state,
                status="needs_review", batch_id=batch_id,
            )
            db.add(new_memory)
            conflicts.append({"content": content, "category": category, "conflicts_with_id": existing_id})
            continue

        # action == "new" (or unrecognized -> treat as new)
        new_memory = AiosMemory(
            user_id=user_id, content=encrypt_text(content), category=category,
            source="user_input", confidence=confidence, temporal_state=temporal_state,
            status="active", batch_id=batch_id,
        )
        db.add(new_memory)
        added.append({"content": content, "category": category})

    db.commit()

    return {
        "added": added,
        "updated": updated,
        "conflicts": conflicts,
        "duplicates_skipped": skipped_duplicates,
    }


def get_overview(db: Session, user_id: int) -> Dict[str, Any]:
    active = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .all()
    )

    categories: Dict[str, int] = {}
    for m in active:
        categories[m.category] = categories.get(m.category, 0) + 1

    recent = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(5)
        .all()
    )

    last_updated = max((m.updated_at for m in active if m.updated_at), default=None)

    conversations_used = (
        db.query(func.count(func.distinct(AiosMemory.batch_id)))
        .filter(AiosMemory.user_id == user_id, AiosMemory.batch_id.isnot(None))
        .scalar()
        or 0
    )

    checks_passed = sum(1 for cat in IDENTITY_STRENGTH_CATEGORIES if categories.get(cat, 0) > 0)
    if len(active) >= MIN_MEMORIES_FOR_STRENGTH_CHECK:
        checks_passed += 1
    total_checks = len(IDENTITY_STRENGTH_CATEGORIES) + 1
    strength_score = round((checks_passed / total_checks) * 100)

    if strength_score >= 75:
        strength_label = "Strong"
    elif strength_score >= 40:
        strength_label = "Growing"
    else:
        strength_label = "Weak"

    return {
        "total_memories": len(active),
        "categories": categories,
        "last_updated": last_updated.isoformat() if last_updated else None,
        "conversations_used": conversations_used,
        "identity_strength": {"score": strength_score, "label": strength_label},
        "recent_memories": [
            {"id": m.id, "content": decrypt_text(m.content), "category": m.category, "updated_at": m.updated_at.isoformat() if m.updated_at else None}
            for m in recent
        ],
    }


def get_memories(db: Session, user_id: int, category: str | None = None) -> List[Dict[str, Any]]:
    query = db.query(AiosMemory).filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
    if category:
        query = query.filter(AiosMemory.category == category)

    results = query.order_by(desc(AiosMemory.updated_at)).all()
    return [
        {
            "id": m.id, "content": decrypt_text(m.content), "category": m.category,
            "confidence": m.confidence,
            "temporal_state": m.temporal_state,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in results
    ]


def get_conflicts(db: Session, user_id: int) -> List[Dict[str, Any]]:
    """Items AIOS held back rather than silently merging - see the
    "conflict" action in CLASSIFY_SYSTEM_PROMPT. No route/UI wired to
    this yet; add one when you're ready to let the user resolve these."""
    results = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "needs_review")
        .order_by(desc(AiosMemory.updated_at))
        .all()
    )
    return [
        {
            "id": m.id, "content": decrypt_text(m.content), "category": m.category,
            "confidence": m.confidence, "temporal_state": m.temporal_state,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in results
    ]


def update_memory(db: Session, user_id: int, memory_id: int, content: str) -> Dict[str, Any]:
    memory = (
        db.query(AiosMemory)
        .filter(AiosMemory.id == memory_id, AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .first()
    )
    if not memory:
        raise AiosError("Memory not found")

    plain_content = content.strip()
    memory.content = encrypt_text(plain_content)
    db.add(memory)
    db.commit()
    return {"id": memory.id, "content": plain_content, "category": memory.category}


def delete_memory(db: Session, user_id: int, memory_id: int) -> None:
    memory = (
        db.query(AiosMemory)
        .filter(AiosMemory.id == memory_id, AiosMemory.user_id == user_id)
        .first()
    )
    if not memory:
        raise AiosError("Memory not found")

    db.delete(memory)
    db.commit()


def get_context_for_quick_prompt(db: Session, user_id: int, limit: int = 30) -> List[str]:
    """Lightweight AIOS context fetch for the main (non-AIOS) Quick Prompt
    endpoint - just the user's most recently touched active memories, no
    separate relevance-ranking LLM call. Relevance filtering happens
    inside the single Quick Prompt generation call instead (see
    quick_prompt.py) to avoid doubling the LLM calls - and therefore the
    real cost - behind a flat-price feature. Distinct from
    get_relevant_memories below, which AIOS Quick Prompt still uses for
    its own separate, more precise two-call flow."""
    memories = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(limit)
        .all()
    )
    return [decrypt_text(m.content) for m in memories]


def get_relevant_memories(db: Session, user_id: int, request_text: str, max_items: int = 10) -> List[str]:
    """
    Used by AIOS Quick Prompt: ranks the user's active memories by
    relevance to the current request via one LLM call, returning only the
    strongest matches rather than dumping the whole identity into the prompt.
    """
    active = (
        db.query(AiosMemory)
        .filter(AiosMemory.user_id == user_id, AiosMemory.status == "active")
        .order_by(desc(AiosMemory.updated_at))
        .limit(200)
        .all()
    )
    if not active:
        return []

    memory_lines = "\n".join(f"id={m.id}: {decrypt_text(m.content)}" for m in active)
    system_prompt = (
        "You are ranking a user's stored identity memories by relevance to their "
        'current request. Return ONLY a JSON object: {"relevant_ids": [id, id, ...]} '
        f"with at most {max_items} ids, ordered most-relevant first. Ignore memories "
        "that don't meaningfully help with this specific request."
    )
    user_content = f"REQUEST:\n{request_text}\n\nMEMORIES:\n{memory_lines}"

    try:
        raw = call_llm(system_prompt, user_content)
        parsed = parse_llm_json(raw)
        relevant_ids = parsed.get("relevant_ids", [])
    except LLMProviderError:
        return []

    by_id = {m.id: decrypt_text(m.content) for m in active}
    return [by_id[i] for i in relevant_ids if i in by_id][:max_items]


def generate_aios_quick_prompt(db: Session, user_id: int, request_text: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:
    """
    AIOS Quick Prompt pipeline: understand request -> retrieve relevant
    identity -> build context -> generate prompt -> validate -> return.
    AIOS never executes the generated prompt itself; the user copies it
    into whichever AI they choose.
    """
    request_text = (request_text or "").strip()
    if not request_text:
        raise AiosError("Tell AIOS what you need first")
    if len(request_text) > MAX_QUICK_PROMPT_LENGTH:
        raise AiosError("That's too long — please trim it")

    relevant_memories = get_relevant_memories(db, user_id, request_text)
    identity_block = (
        "\n".join(f"- {m}" for m in relevant_memories)
        if relevant_memories
        else "(AIOS doesn't know much about this user yet)"
    )

    user_content = f"USER REQUEST:\n{request_text}\n\nUSER IDENTITY:\n{identity_block}"

    raw = call_llm(AIOS_QUICK_PROMPT_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)
    parsed = parse_llm_json(raw)

    prompt = parsed.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AiosError("AIOS couldn't generate a prompt — please try again")

    return {"prompt": prompt.strip()}


def record_edit_feedback(db: Session, user_id: int, original: str, edited: str) -> None:
    """
    Quick Prompt edit-feedback loop (algorithm steps 45-46). Called when
    a signed-in user copies a Quick Prompt result they've edited.
    Classifies the edit into a fixed pattern (or "none"), increments a
    per-user per-pattern counter, and only once that pattern has shown
    up across EDIT_PATTERN_REPEAT_THRESHOLD separate sessions does it
    write an actual AiosMemory - at low confidence and temporal_state
    "current", since this is an inference, not something the user
    stated. Best-effort: any failure here should never surface to the
    user or block the Copy action, so callers should wrap this in a
    try/except and swallow errors, same pattern as get_relevant_memories.
    """
    from services.models import AiosEditPattern

    original = (original or "").strip()
    edited = (edited or "").strip()
    if not original or not edited or original == edited:
        return

    user_content = f"ORIGINAL:\n{original}\n\nEDITED:\n{edited}"
    raw = call_llm(EDIT_CLASSIFY_SYSTEM_PROMPT, user_content)
    parsed = parse_llm_json(raw)

    pattern_key = parsed.get("pattern_key")
    if pattern_key not in EDIT_PATTERN_KEYS or pattern_key == "none":
        return

    row = (
        db.query(AiosEditPattern)
        .filter(AiosEditPattern.user_id == user_id, AiosEditPattern.pattern_key == pattern_key)
        .with_for_update()
        .first()
    )
    if not row:
        row = AiosEditPattern(user_id=user_id, pattern_key=pattern_key, occurrences=0)
        db.add(row)
        db.flush()

    row.occurrences += 1

    if row.occurrences >= EDIT_PATTERN_REPEAT_THRESHOLD:
        content = f"User {EDIT_PATTERN_DESCRIPTIONS[pattern_key]} (observed across repeated Quick Prompt edits)."
        memory = AiosMemory(
            user_id=user_id, content=encrypt_text(content), category="writing_style",
            source="inferred_edit_pattern", confidence="low", temporal_state="current",
            status="active",
        )
        db.add(memory)
        row.occurrences = 0

    db.commit()
