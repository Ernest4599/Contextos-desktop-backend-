# --- 1. services/llm_providers.py: add allowed_providers filter to call_llm ---
p = "services/llm_providers.py"
s = open(p).read()
old = '''def call_llm(system_prompt: str, user_content: str) -> str:
    preferred = os.environ.get("LLM_PROVIDER", "").lower()

    order: list[str] = []
    if preferred in _PROVIDERS:
        order.append(preferred)
    for name in _PROVIDER_ORDER:
        if name not in order:
            order.append(name)'''
new = '''def call_llm(system_prompt: str, user_content: str, allowed_providers: list[str] | None = None) -> str:
    preferred = os.environ.get("LLM_PROVIDER", "").lower()

    order: list[str] = []
    if preferred in _PROVIDERS:
        order.append(preferred)
    for name in _PROVIDER_ORDER:
        if name not in order:
            order.append(name)

    if allowed_providers is not None:
        order = [name for name in order if name in allowed_providers]'''
assert s.count(old) == 1, "llm_providers.py: call_llm anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("llm_providers.py patched")


# --- 2. services/license_service.py: add more_context plan ---
p = "services/license_service.py"
s = open(p).read()

old_valid = 'VALID_PLANS = ["free", "pro", "team"]'
new_valid = 'VALID_PLANS = ["free", "pro", "more_context", "team"]'
assert s.count(old_valid) == 1, "license_service.py: VALID_PLANS anchor not found/not unique"
s = s.replace(old_valid, new_valid, 1)

old_limits = '''PLAN_CREDIT_LIMITS = {"pro": 300}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.'''
new_limits = '''PLAN_CREDIT_LIMITS = {"pro": 300, "more_context": 600}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered.
PLAN_PROVIDERS = {"more_context": ["anthropic", "openai"]}'''
assert s.count(old_limits) == 1, "license_service.py: PLAN_CREDIT_LIMITS anchor not found/not unique"
s = s.replace(old_limits, new_limits, 1)

open(p, "w").write(s)
print("license_service.py patched")


# --- 3. services/quick_prompt.py: thread allowed_providers through ---
p = "services/quick_prompt.py"
s = open(p).read()
old = '''def generate_quick_prompt(overview: str, decisions: str, task: str) -> Dict[str, Any]:
    validate_quick_prompt_input(overview, decisions, task)

    user_content = (
        f"OVERVIEW:\\n{(overview or '').strip() or '(none provided)'}\\n\\n"
        f"DECISIONS:\\n{(decisions or '').strip() or '(none provided)'}\\n\\n"
        f"TASK:\\n{(task or '').strip()}"
    )

    raw = call_llm(QUICK_PROMPT_SYSTEM_PROMPT, user_content)'''
new = '''def generate_quick_prompt(overview: str, decisions: str, task: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:
    validate_quick_prompt_input(overview, decisions, task)

    user_content = (
        f"OVERVIEW:\\n{(overview or '').strip() or '(none provided)'}\\n\\n"
        f"DECISIONS:\\n{(decisions or '').strip() or '(none provided)'}\\n\\n"
        f"TASK:\\n{(task or '').strip()}"
    )

    raw = call_llm(QUICK_PROMPT_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)'''
assert s.count(old) == 1, "quick_prompt.py: generate_quick_prompt anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("quick_prompt.py patched")


# --- 4. services/context_extractor.py: thread allowed_providers through ---
p = "services/context_extractor.py"
s = open(p).read()
old = '''def extract_context(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    transcript = _messages_to_transcript(messages)
    if not transcript.strip():
        raise ExtractionError("No message content to extract from")

    raw = call_llm(EXTRACTION_SYSTEM_PROMPT, transcript)'''
new = '''def extract_context(messages: List[Dict[str, str]], allowed_providers: list[str] | None = None) -> Dict[str, Any]:
    transcript = _messages_to_transcript(messages)
    if not transcript.strip():
        raise ExtractionError("No message content to extract from")

    raw = call_llm(EXTRACTION_SYSTEM_PROMPT, transcript, allowed_providers=allowed_providers)'''
assert s.count(old) == 1, "context_extractor.py: extract_context anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("context_extractor.py patched")


# --- 5. services/processing_pipeline.py: thread allowed_providers through ---
p = "services/processing_pipeline.py"
s = open(p).read()
old = '''async def run_processing_pipeline(messages: List[Dict[str, str]]):
    total = len(messages)'''
new = '''async def run_processing_pipeline(messages: List[Dict[str, str]], allowed_providers: list[str] | None = None):
    total = len(messages)'''
assert s.count(old) == 1, "processing_pipeline.py: signature anchor not found/not unique"
s = s.replace(old, new, 1)

old_call = '''    try:
        extracted = extract_context(messages)'''
new_call = '''    try:
        extracted = extract_context(messages, allowed_providers=allowed_providers)'''
assert s.count(old_call) == 1, "processing_pipeline.py: extract_context call anchor not found/not unique"
s = s.replace(old_call, new_call, 1)

open(p, "w").write(s)
print("processing_pipeline.py patched")


# --- 6. main.py: thread allowed_providers through all call sites ---
p = "main.py"
s = open(p).read()

# 6a. _pipeline_with_autosave signature + internal run_processing_pipeline call
old_sig = "async def _pipeline_with_autosave(messages, access: AccessContext, source: str, is_metered: bool = False, license_id: int | None = None):"
new_sig = "async def _pipeline_with_autosave(messages, access: AccessContext, source: str, is_metered: bool = False, license_id: int | None = None, allowed_providers: list[str] | None = None):"
assert s.count(old_sig) == 1, "main.py: _pipeline_with_autosave signature anchor not found/not unique"
s = s.replace(old_sig, new_sig, 1)

old_inner_call = "    async for chunk in run_processing_pipeline(messages):"
new_inner_call = "    async for chunk in run_processing_pipeline(messages, allowed_providers=allowed_providers):"
assert s.count(old_inner_call) == 1, "main.py: run_processing_pipeline call anchor not found/not unique"
s = s.replace(old_inner_call, new_inner_call, 1)

# 6b. quick-prompt route
old_qp = "        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task)"
new_qp = '''        allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
        result = generate_quick_prompt(payload.overview, payload.decisions, payload.task, allowed_providers=allowed_providers)'''
assert s.count(old_qp) == 1, "main.py: quick_prompt call anchor not found/not unique"
s = s.replace(old_qp, new_qp, 1)

# 6c. process_paste StreamingResponse call site
old_paste = '''    messages = split_messages(validated)
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id),
        media_type="text/event-stream",
    )'''
new_paste = '''    messages = split_messages(validated)
    allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id, allowed_providers=allowed_providers),
        media_type="text/event-stream",
    )'''
assert s.count(old_paste) == 1, "main.py: process_paste call anchor not found/not unique"
s = s.replace(old_paste, new_paste, 1)

# 6d. process_upload StreamingResponse call site (distinguished by trailing comment)
old_upload = '''    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id),
        media_type="text/event-stream",
    )


# TEMPORARY DEBUG: test multiple curl_cffi impersonate values from Render itself'''
new_upload = '''    allowed_providers = license_service.PLAN_PROVIDERS.get(access.plan)
    return StreamingResponse(
        _pipeline_with_autosave(messages, access, source="import", is_metered=is_metered, license_id=access.license_id, allowed_providers=allowed_providers),
        media_type="text/event-stream",
    )


# TEMPORARY DEBUG: test multiple curl_cffi impersonate values from Render itself'''
assert s.count(old_upload) == 1, "main.py: process_upload call anchor not found/not unique"
s = s.replace(old_upload, new_upload, 1)

open(p, "w").write(s)
print("main.py patched (6 sites)")
