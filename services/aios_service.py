"""
AIOS memory engine: Input -> Understand -> Extract -> Classify -> Store -> Retrieve.

Deliberately simple per the MVP spec: one LLM call extracts discrete
memory items from free text, classifies each into a fixed category set,
and decides against the user's existing memories whether each item is
new, a duplicate (touch existing), or a conflicting update (mark old
outdated, store new as current). No separate embedding/similarity
system - the LLM does this classification directly, same pattern as
context_extractor.py and quick_prompt.py.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session
from sqlalchemy import desc

from services.llm_providers import LLMProviderError, call_llm, parse_llm_json
from services.models import AiosMemory

AiosError = LLMProviderError

ALLOWED_CATEGORIES = [
    "personality", "preference", "goal", "interest",
    "knowledge", "writing_style", "important_fact", "context",
]

MAX_EXISTING_MEMORIES_IN_PROMPT = 60

CLASSIFY_SYSTEM_PROMPT = """You are AIOS, an identity layer that learns what matters about a user from what they tell you, to later personalize AI prompts on their behalf.

Given new input from the user and a list of their existing stored memories, do the following:

1. Extract each discrete, useful piece of information from the input as a separate item. A single message can contain multiple items (e.g. a preference AND a goal). Discard filler that carries no lasting information about the user (greetings, small talk).
2. For each item, classify it into exactly one category from this fixed list: personality, preference, goal, interest, knowledge, writing_style, important_fact, context.
3. For each item, decide an action by comparing it to the existing memories provided:
   - "new" - genuinely new information, no matching existing memory
   - "duplicate" - restates an existing memory with the same meaning (include the matching existing memory's id)
   - "update" - conflicts with or supersedes an existing memory in the same category (e.g. old: "prefers detailed explanations", new: "prefers concise explanations") - include the existing memory's id to mark outdated

Respond with ONLY a JSON object of this exact shape:
{"items": [{"content": "...", "category": "...", "action": "new"}, {"content": "...", "category": "...", "action": "update", "existing_id": 12}, {"content": "...", "category": "...", "action": "duplicate", "existing_id": 7}]}

If nothing useful is present, return {"items": []}. No preamble, no markdown fences."""


def _format_existing_memories(memories: List[AiosMemory]) -> str:
    if not memories:
        return "(none yet)"
    lines = [f"id={m.id} category={m.category} content=\"{m.content}\"" for m in memories]
    return "\n".join(lines)


def tell_aios(db: Session, user_id: int, raw_input: str) -> Dict[str, Any]:
    raw_input = (raw_input or "").strip()
    if not raw_input:
        raise AiosError("Tell AIOS something first")

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

    raw = call_llm(CLASSIFY_SYSTEM_PROMPT, user_content)
    parsed = parse_llm_json(raw)
    items = parsed.get("items", [])
    if not isinstance(items, list):
        items = []

    existing_by_id = {m.id: m for m in existing}
    added: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    skipped_duplicates = 0

    for item in items:
        content = (item.get("content") or "").strip()
        category = item.get("category")
        action = item.get("action", "new")

        if not content or category not in ALLOWED_CATEGORIES:
            continue

        if action == "duplicate":
            existing_id = item.get("existing_id")
            match = existing_by_id.get(existing_id)
            if match:
                match.updated_at = None  # let onupdate trigger a fresh timestamp
                db.add(match)
            skipped_duplicates += 1
            continue

        if action == "update":
            existing_id = item.get("existing_id")
            match = existing_by_id.get(existing_id)
            if match:
                match.status = "outdated"
                db.add(match)
            new_memory = AiosMemory(
                user_id=user_id, content=content, category=category,
                source="user_input", confidence="high", status="active",
            )
            db.add(new_memory)
            updated.append({"content": content, "category": category})
            continue

        # action == "new" (or unrecognized -> treat as new)
        new_memory = AiosMemory(
            user_id=user_id, content=content, category=category,
            source="user_input", confidence="high", status="active",
        )
        db.add(new_memory)
        added.append({"content": content, "category": category})

    db.commit()

    return {
        "added": added,
        "updated": updated,
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

    return {
        "total_memories": len(active),
        "categories": categories,
        "recent_memories": [
            {"id": m.id, "content": m.content, "category": m.category, "updated_at": m.updated_at.isoformat() if m.updated_at else None}
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
            "id": m.id, "content": m.content, "category": m.category,
            "confidence": m.confidence,
            "created_at": m.created_at.isoformat() if m.created_at else None,
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

    memory.content = content.strip()
    db.add(memory)
    db.commit()
    return {"id": memory.id, "content": memory.content, "category": memory.category}


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


def get_relevant_memories(db: Session, user_id: int, request_text: str, max_items: int = 10) -> List[str]:
    """
    Used by Quick Prompt's AIOS mode: ranks the user's active memories by
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

    memory_lines = "\n".join(f"id={m.id}: {m.content}" for m in active)
    system_prompt = (
        "You are ranking a user's stored identity memories by relevance to their "
        "current request. Return ONLY a JSON object: {\"relevant_ids\": [id, id, ...]} "
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

    by_id = {m.id: m.content for m in active}
    return [by_id[i] for i in relevant_ids if i in by_id][:max_items]
