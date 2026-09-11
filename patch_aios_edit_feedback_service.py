p = "services/aios_service.py"
s = open(p).read()

# 1. Add the fixed pattern vocabulary + repeat threshold near the other constants
old_const = '''MIN_MEMORIES_FOR_STRENGTH_CHECK = 10'''
new_const = '''MIN_MEMORIES_FOR_STRENGTH_CHECK = 10

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

Respond with ONLY a JSON object: {{"pattern_key": "..."}} using exactly one of the keys above (including "none"). No preamble, no markdown fences, no extra commentary."""'''
assert s.count(old_const) == 1, "aios_service.py: MIN_MEMORIES_FOR_STRENGTH_CHECK anchor not found/not unique"
s = s.replace(old_const, new_const, 1)

# 2. Append record_edit_feedback at the end of the file
old_end = '''    prompt = parsed.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AiosError("AIOS couldn't generate a prompt — please try again")

    return {"prompt": prompt.strip()}'''
new_end = '''    prompt = parsed.get("prompt", "")
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

    user_content = f"ORIGINAL:\\n{original}\\n\\nEDITED:\\n{edited}"
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

    db.commit()'''
assert s.count(old_end) == 1, "aios_service.py: end-of-file anchor not found/not unique"
s = s.replace(old_end, new_end, 1)

open(p, "w").write(s)
print("aios_service.py patched")
