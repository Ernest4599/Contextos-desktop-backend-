# --- 1. services/models.py: add AIOS daily counter columns to License ---
p = "services/models.py"
s = open(p).read()
old = '''    credits_remaining = Column(Integer, nullable=True)  # only set for metered plans (pro) - see PLAN_CREDIT_LIMITS'''
new = '''    credits_remaining = Column(Integer, nullable=True)  # only set for metered plans (pro) - see PLAN_CREDIT_LIMITS
    aios_actions_today = Column(Integer, nullable=True, default=0)  # only used by plans with an AIOS daily cap (e.g. pro_account)
    last_aios_reset_date = Column(DateTime(timezone=True), nullable=True)'''
assert s.count(old) == 1, "models.py: License columns anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("models.py patched")


# --- 2. services/db.py: migration for the two new columns ---
p = "services/db.py"
s = open(p).read()
old = '        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS credits_remaining INTEGER"))'
new = old + '''
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS aios_actions_today INTEGER DEFAULT 0"))
        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS last_aios_reset_date TIMESTAMPTZ"))'''
assert s.count(old) == 1, "db.py: credits_remaining migration anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("db.py patched")


# --- 3. services/license_service.py: pro_account plan + AIOS daily-reset gate ---
p = "services/license_service.py"
s = open(p).read()

old_valid = 'VALID_PLANS = ["free", "pro", "more_context", "team"]'
new_valid = 'VALID_PLANS = ["free", "pro", "pro_account", "more_context", "team"]'
assert s.count(old_valid) == 1, "license_service.py: VALID_PLANS anchor not found/not unique"
s = s.replace(old_valid, new_valid, 1)

old_limits = '''PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "more_context": 600}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered.
PLAN_PROVIDERS = {"free": ["gemini"], "more_context": ["anthropic", "openai"]}'''
new_limits = '''PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "pro_account": 400, "more_context": 600}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt, Import, or AIOS - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered. pro_account is intentionally
# absent here - it allows all configured providers.
PLAN_PROVIDERS = {"free": ["gemini"], "more_context": ["anthropic", "openai"]}

# Plans with a daily AIOS action cap, separate from and in addition to
# their credit pool - an AIOS action costs credits AND counts against
# this daily count. A plan not listed here has no AIOS-specific daily
# cap (though AIOS may still be blocked entirely for that plan - see
# _require_aios_access in main.py).
PLAN_AIOS_DAILY_LIMIT = {"pro_account": 30}'''
assert s.count(old_limits) == 1, "license_service.py: PLAN_CREDIT_LIMITS anchor not found/not unique"
s = s.replace(old_limits, new_limits, 1)

old_end = '''def get_or_create_free_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:'''
new_end = '''def _maybe_reset_aios_daily_count(lic: License) -> None:
    """Resets the AIOS daily counter if we've crossed into a new UTC day
    since last_aios_reset_date. Caller must hold the row lock already."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc)
    if lic.last_aios_reset_date is None or lic.last_aios_reset_date.date() != now.date():
        lic.aios_actions_today = 0
        lic.last_aios_reset_date = now


def check_and_reserve_aios_action(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
    """Call before an AIOS action (Tell AIOS or AIOS Quick Prompt) on a
    plan with a daily AIOS cap. Raises LicenseError if blocked, otherwise
    atomically reserves the credits AND increments the daily AIOS count.
    Caller must follow with commit_credits() on success or
    release_credits() on failure - the daily count is NOT released on
    failure, mirroring free_license_service's failed-attempt handling
    (a failed attempt still counts, only the credit is refunded)."""
    lic = _lock_license_by_id(db, license_id)

    if lic.status != "active":
        db.rollback()
        raise LicenseError("This license is not active.")

    _maybe_reset_aios_daily_count(lic)

    daily_limit = PLAN_AIOS_DAILY_LIMIT.get(lic.plan)
    if daily_limit is not None and (lic.aios_actions_today or 0) >= daily_limit:
        db.rollback()
        raise LicenseError("You've reached your daily AIOS usage limit.")

    if lic.credits_remaining is None:
        db.rollback()
        raise LicenseError("This plan doesn't include metered usage.")

    if lic.credits_remaining < cost:
        db.rollback()
        raise LicenseError("You've reached your usage limit.")

    lic.credits_remaining -= cost
    lic.aios_actions_today = (lic.aios_actions_today or 0) + 1
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def get_or_create_free_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:'''
assert s.count(old_end) == 1, "license_service.py: get_or_create_free_license_for_user anchor not found/not unique"
s = s.replace(old_end, new_end, 1)

open(p, "w").write(s)
print("license_service.py patched")


# --- 4. services/aios_service.py: thread allowed_providers through the two LLM-calling functions ---
p = "services/aios_service.py"
s = open(p).read()

old_tell = '''def tell_aios(db: Session, user_id: int, raw_input: str) -> Dict[str, Any]:'''
new_tell = '''def tell_aios(db: Session, user_id: int, raw_input: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:'''
assert s.count(old_tell) == 1, "aios_service.py: tell_aios signature anchor not found/not unique"
s = s.replace(old_tell, new_tell, 1)

old_tell_call = '''    raw = call_llm(CLASSIFY_SYSTEM_PROMPT, user_content)'''
new_tell_call = '''    raw = call_llm(CLASSIFY_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)'''
assert s.count(old_tell_call) == 1, "aios_service.py: tell_aios call_llm anchor not found/not unique"
s = s.replace(old_tell_call, new_tell_call, 1)

old_qp = '''def generate_aios_quick_prompt(db: Session, user_id: int, request_text: str) -> Dict[str, Any]:'''
new_qp = '''def generate_aios_quick_prompt(db: Session, user_id: int, request_text: str, allowed_providers: list[str] | None = None) -> Dict[str, Any]:'''
assert s.count(old_qp) == 1, "aios_service.py: generate_aios_quick_prompt signature anchor not found/not unique"
s = s.replace(old_qp, new_qp, 1)

old_qp_call = '''    raw = call_llm(AIOS_QUICK_PROMPT_SYSTEM_PROMPT, user_content)'''
new_qp_call = '''    raw = call_llm(AIOS_QUICK_PROMPT_SYSTEM_PROMPT, user_content, allowed_providers=allowed_providers)'''
assert s.count(old_qp_call) == 1, "aios_service.py: generate_aios_quick_prompt call_llm anchor not found/not unique"
s = s.replace(old_qp_call, new_qp_call, 1)

open(p, "w").write(s)
print("aios_service.py patched")


# --- 5. main.py: update _require_aios_access + wire reserve/commit/release into aios_tell and aios_quick_prompt ---
p = "main.py"
s = open(p).read()

old_helper = '''def _require_aios_access(user_id: int) -> None:
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
        raise ValueError("AIOS isn't available on your plan. Upgrade to unlock it.")'''

new_helper = '''def _require_aios_access(user_id: int) -> tuple[str, int]:
    """Raises ValueError (caught the same way as _require_user's errors
    by every caller below) if the user's plan doesn't include AIOS.
    Lazily provisions a Start Free license first if the user has none
    yet, mirroring what require_access does for the metered routes.
    Returns (plan, license_id) so callers that need to meter AIOS usage
    (e.g. pro_account) don't have to look the license up again."""
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
            license_id = license.id
        else:
            created = license_service.get_or_create_free_license_for_user(db, user_id)
            plan = created["plan"]
            license_id = created["license_id"]
    finally:
        db.close()

    if plan == "free":
        raise ValueError("AIOS isn't available on your plan. Upgrade to unlock it.")

    return plan, license_id'''

assert s.count(old_helper) == 1, "main.py: _require_aios_access anchor not found/not unique"
s = s.replace(old_helper, new_helper, 1)

# aios_tell: wire reserve/commit/release + allowed_providers
old_tell_route = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.tell_aios(db, user_id, payload.content)
        return {"success": True, **result}'''
new_tell_route = '''        user_id = _require_user(authorization)
        plan, license_id = _require_aios_access(user_id)
        from services import license_service

        is_aios_metered = plan in license_service.PLAN_AIOS_DAILY_LIMIT
        reserved = False
        if is_aios_metered:
            credit_db = get_db_session()
            try:
                license_service.check_and_reserve_aios_action(credit_db, license_id)
                reserved = True
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                credit_db.close()

        allowed_providers = license_service.PLAN_PROVIDERS.get(plan)
        db = get_db_session()
        try:
            result = aios_service.tell_aios(db, user_id, payload.content, allowed_providers=allowed_providers)
        except Exception:
            if is_aios_metered and reserved:
                release_db = get_db_session()
                try:
                    license_service.release_credits(release_db, license_id)
                finally:
                    release_db.close()
            raise

        if is_aios_metered and reserved:
            commit_db = get_db_session()
            try:
                license_service.commit_credits(commit_db, license_id)
            finally:
                commit_db.close()

        return {"success": True, **result}'''
assert s.count(old_tell_route) == 1, "main.py: aios_tell route body anchor not found/not unique"
s = s.replace(old_tell_route, new_tell_route, 1)

# aios_quick_prompt: wire reserve/commit/release + allowed_providers
old_qp_route = '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.generate_aios_quick_prompt(db, user_id, payload.message)'''
new_qp_route = '''        user_id = _require_user(authorization)
        plan, license_id = _require_aios_access(user_id)
        from services import license_service

        is_aios_metered = plan in license_service.PLAN_AIOS_DAILY_LIMIT
        reserved = False
        if is_aios_metered:
            credit_db = get_db_session()
            try:
                license_service.check_and_reserve_aios_action(credit_db, license_id)
                reserved = True
            except license_service.LicenseError as e:
                return {"success": False, "error": e.message}
            finally:
                credit_db.close()

        allowed_providers = license_service.PLAN_PROVIDERS.get(plan)
        db = get_db_session()
        try:
            result = aios_service.generate_aios_quick_prompt(db, user_id, payload.message, allowed_providers=allowed_providers)
        except Exception:
            if is_aios_metered and reserved:
                release_db = get_db_session()
                try:
                    license_service.release_credits(release_db, license_id)
                finally:
                    release_db.close()
            raise

        if is_aios_metered and reserved:
            commit_db = get_db_session()
            try:
                license_service.commit_credits(commit_db, license_id)
            finally:
                commit_db.close()'''
assert s.count(old_qp_route) == 1, "main.py: aios_quick_prompt route body anchor not found/not unique"
s = s.replace(old_qp_route, new_qp_route, 1)

# Other AIOS routes (_require_aios_access is now called there too - tuple unpack needed)
for route_name, old_call in [
    ("aios_overview", '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.get_overview(db, user_id)'''),
    ("aios_memories", '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        results = aios_service.get_memories(db, user_id, category)'''),
    ("aios_update_memory", '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        result = aios_service.update_memory(db, user_id, memory_id, payload.content)'''),
    ("aios_delete_memory", '''        user_id = _require_user(authorization)
        _require_aios_access(user_id)
        db = get_db_session()
        aios_service.delete_memory(db, user_id, memory_id)'''),
]:
    assert s.count(old_call) == 1, f"main.py: {route_name} anchor not found/not unique"
    new_call = old_call.replace("_require_aios_access(user_id)", "_require_aios_access(user_id)  # plan/license_id unused here - no metering on read/write-only AIOS routes")
    s = s.replace(old_call, new_call, 1)

open(p, "w").write(s)
print("main.py patched (_require_aios_access + aios_tell + aios_quick_prompt + 4 unchanged call sites annotated)")
