# --- 1. services/license_service.py: add free plan config + lazy-create function ---
p = "services/license_service.py"
s = open(p).read()

old_limits = '''PLAN_CREDIT_LIMITS = {"pro": 300, "more_context": 600}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered.
PLAN_PROVIDERS = {"more_context": ["anthropic", "openai"]}'''
new_limits = '''PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "more_context": 600}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered.
PLAN_PROVIDERS = {"free": ["gemini"], "more_context": ["anthropic", "openai"]}'''
assert s.count(old_limits) == 1, "license_service.py: PLAN_CREDIT_LIMITS anchor not found/not unique"
s = s.replace(old_limits, new_limits, 1)

old_end = '''def mask_license_key(key: str) -> str:'''
new_end = '''def get_or_create_free_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:
    """Lazily provisions a signed-in user's Start Free license the first
    time their access is resolved and they don't already have one -
    called from access_control.py so a signed-in user's plan is never
    None. Never downgrades an existing paid plan; only creates when no
    active license exists at all for this user_id."""
    license = (
        db.query(License)
        .filter(License.user_id == user_id, License.status == "active")
        .order_by(License.created_at.desc())
        .first()
    )
    if license:
        return _serialize(license)
    return create_license_after_payment(db, user_id, "free")


def mask_license_key(key: str) -> str:'''
assert s.count(old_end) == 1, "license_service.py: mask_license_key anchor not found/not unique"
s = s.replace(old_end, new_end, 1)

open(p, "w").write(s)
print("license_service.py patched")


# --- 2. services/access_control.py: lazy-create Start Free on first session access ---
p = "services/access_control.py"
s = open(p).read()
old = '''                if owned_license:
                    license_id = owned_license.id
                    plan = owned_license.plan
            finally:
                db.close()'''
new = '''                if owned_license:
                    license_id = owned_license.id
                    plan = owned_license.plan
                else:
                    from services import license_service
                    created = license_service.get_or_create_free_license_for_user(db, user_id)
                    license_id = created["license_id"]
                    plan = created["plan"]
            finally:
                db.close()'''
assert s.count(old) == 1, "access_control.py: owned_license anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("access_control.py patched")


# --- 3. main.py: add _require_aios_access helper + gate all AIOS routes ---
p = "main.py"
s = open(p).read()

old_helper_anchor = '''class TellAiosRequest(BaseModel):
    content: str'''
new_helper_anchor = '''def _require_aios_access(user_id: int) -> None:
    """Raises ValueError (caught the same way as _require_user's errors
    by every caller below) if the user's plan doesn't include AIOS.
    Lazily provisions a Start Free license first if the user has none
    yet, mirroring what require_access does for the metered routes."""
    from services.db import get_db_session
    from services import license_service

    db = get_db_session()
    try:
        license = (
            db.query(License)
            .filter(License.user_id == user_id, License.status == "active")
            .order_by(License.created_at.desc())
            .first()
        )
        if license:
            plan = license.plan
        else:
            created = license_service.get_or_create_free_license_for_user(db, user_id)
            plan = created["plan"]
    finally:
        db.close()

    if plan == "free":
        raise ValueError("AIOS isn't available on your plan. Upgrade to unlock it.")


class TellAiosRequest(BaseModel):
    content: str'''
assert s.count(old_helper_anchor) == 1, "main.py: TellAiosRequest anchor not found/not unique"
s = s.replace(old_helper_anchor, new_helper_anchor, 1)

# aios_tell
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.tell_aios(db, user_id, payload.content)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.tell_aios(db, user_id, payload.content)'''
assert s.count(old) == 1, "main.py: aios_tell anchor not found/not unique"
s = s.replace(old, new, 1)

# aios_overview
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)'''
assert s.count(old) == 1, "main.py: aios_overview anchor not found/not unique"
s = s.replace(old, new, 1)

# aios_memories
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        results = aios_service.get_memories(db, user_id, category)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        results = aios_service.get_memories(db, user_id, category)'''
assert s.count(old) == 1, "main.py: aios_memories anchor not found/not unique"
s = s.replace(old, new, 1)

# aios_update_memory
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.update_memory(db, user_id, memory_id, payload.content)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.update_memory(db, user_id, memory_id, payload.content)'''
assert s.count(old) == 1, "main.py: aios_update_memory anchor not found/not unique"
s = s.replace(old, new, 1)

# aios_delete_memory
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        aios_service.delete_memory(db, user_id, memory_id)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        aios_service.delete_memory(db, user_id, memory_id)'''
assert s.count(old) == 1, "main.py: aios_delete_memory anchor not found/not unique"
s = s.replace(old, new, 1)

# aios_quick_prompt
old = '''        user_id = _require_user(authorization)
        db = get_db_session()
        result = aios_service.generate_aios_quick_prompt(db, user_id, payload.message)'''
new = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.generate_aios_quick_prompt(db, user_id, payload.message)'''
assert s.count(old) == 1, "main.py: aios_quick_prompt anchor not found/not unique"
s = s.replace(old, new, 1)

open(p, "w").write(s)
print("main.py patched (helper + 6 AIOS routes)")
