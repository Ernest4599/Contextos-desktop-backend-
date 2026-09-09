# --- 1. services/aios_service.py: lightweight memory fetch, no LLM call ---
p = "services/aios_service.py"
s = open(p).read()
old = '''def get_relevant_memories(db: Session, user_id: int, request_text: str, max_items: int = 10) -> List[str]:'''
new = '''def get_context_for_quick_prompt(db: Session, user_id: int, limit: int = 30) -> List[str]:
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


def get_relevant_memories(db: Session, user_id: int, request_text: str, max_items: int = 10) -> List[str]:'''
assert s.count(old) == 1, "aios_service.py: get_relevant_memories anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("aios_service.py patched")


# --- 2. services/quick_prompt.py: AIOS-aware system prompt + generation ---
p = "services/quick_prompt.py"
s = open(p).read()

old_prompt_end = '''Respond with ONLY a JSON object with these exact keys:
- "role": string, the expert role selected
- "prompt": string, the complete final prompt ready to paste into any AI
- "assumptions": array of strings, any assumptions made (empty array if none)
- "output_format": string, the output format chosen

No preamble, no markdown fences, no extra commentary."""'''

new_prompt_end = '''If EXISTING USER CONTEXT is provided below the three inputs, it comes from what this specific user has previously told the system about themselves (preferences, goals, working style, background). Use it under these rules:
- The Overview / Decisions / Task the user just gave you always take priority. If EXISTING USER CONTEXT conflicts with anything in Overview, Decisions, or Task, the current input wins - never let old context override what the user is asking for right now.
- Only use a piece of EXISTING USER CONTEXT if it is genuinely relevant to this specific request. Most of it usually will not be. Ignore anything irrelevant, even if it's interesting - do not force personalization in.
- Weave relevant context in naturally, as if you already knew this about the user - never write phrases like "the system knows" or "according to your profile" or mention memories, confidence scores, or any internal source. Translate it into plain context (e.g. write "keep the writing concise" rather than "user prefers concise writing per stored preference").
- If nothing in EXISTING USER CONTEXT is relevant, ignore it entirely and say so via used_personalization: false.

Respond with ONLY a JSON object with these exact keys:
- "role": string, the expert role selected
- "prompt": string, the complete final prompt ready to paste into any AI
- "assumptions": array of strings, any assumptions made (empty array if none)
- "output_format": string, the output format chosen
- "used_personalization": boolean, true only if you actually incorporated something from EXISTING USER CONTEXT into the final prompt

No preamble, no markdown fences, no extra commentary."""'''

assert s.count(old_prompt_end) == 1, "quick_prompt.py: system prompt ending anchor not found/not unique"
s = s.replace(old_prompt_end, new_prompt_end, 1)

old_fn = '''def generate_quick_prompt(overview: str, decisions: str, task: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:
    validate_quick_prompt_input(overview, decisions, task)

    user_content = (
        f"OVERVIEW:\\n{(overview or '').strip() or '(none provided)'}\\n\\n"
        f"DECISIONS:\\n{(decisions or '').strip() or '(none provided)'}\\n\\n"
        f"TASK:\\n{(task or '').strip()}"
    )

    raw = call_llm(QUICK_PROMPT_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)
    parsed = parse_llm_json(raw)

    return {
        "role": parsed.get("role", ""),
        "prompt": parsed.get("prompt", ""),
        "assumptions": parsed.get("assumptions", []) if isinstance(parsed.get("assumptions"), list) else [],
        "output_format": parsed.get("output_format", ""),
    }'''

new_fn = '''def generate_quick_prompt(
    overview: str,
    decisions: str,
    task: str,
    allowed_providers: list[str] | None = None,
    aios_context: list[str] | None = None,
) -> Dict[str, Any]:
    validate_quick_prompt_input(overview, decisions, task)

    user_content = (
        f"OVERVIEW:\\n{(overview or '').strip() or '(none provided)'}\\n\\n"
        f"DECISIONS:\\n{(decisions or '').strip() or '(none provided)'}\\n\\n"
        f"TASK:\\n{(task or '').strip()}"
    )

    if aios_context:
        context_block = "\\n".join(f"- {c}" for c in aios_context)
        user_content += f"\\n\\nEXISTING USER CONTEXT:\\n{context_block}"

    raw = call_llm(QUICK_PROMPT_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)
    parsed = parse_llm_json(raw)

    return {
        "role": parsed.get("role", ""),
        "prompt": parsed.get("prompt", ""),
        "assumptions": parsed.get("assumptions", []) if isinstance(parsed.get("assumptions"), list) else [],
        "output_format": parsed.get("output_format", ""),
        "used_aios": bool(parsed.get("used_personalization", False)),
    }'''

assert s.count(old_fn) == 1, "quick_prompt.py: generate_quick_prompt anchor not found/not unique"
s = s.replace(old_fn, new_fn, 1)

open(p, "w").write(s)
print("quick_prompt.py patched")


# --- 3. main.py: retrieve AIOS context for signed-in, AIOS-entitled users ---
p = "main.py"
s = open(p).read()

old = '''        allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task, allowed_providers=allowed_providers)'''

new = '''        allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)

        aios_context = None
        if access.via == "session" and access.plan not in (None, "free"):
            from services import aios_service
            aios_db = get_db_session()
            try:
                aios_context = aios_service.get_context_for_quick_prompt(aios_db, access.user_id)
            except Exception as e:
                print(f"[QUICK_PROMPT] Failed to fetch AIOS context, continuing without it: {e}")
                aios_context = None
            finally:
                aios_db.close()

        result = generate_quick_prompt(
            payload.overview, payload.decisions, payload.task,
            allowed_providers=allowed_providers, aios_context=aios_context,
        )'''

assert s.count(old) == 1, "main.py: quick_prompt generation call anchor not found/not unique"
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("main.py patched")
