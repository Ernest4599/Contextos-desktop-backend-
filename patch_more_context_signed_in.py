# --- 1. services/models.py: add separate AIOS credit pool column ---
p = "services/models.py"
s = open(p).read()
old = '''    aios_actions_today = Column(Integer, nullable=True, default=0)  # only used by plans with an AIOS daily cap (e.g. pro_account)
    last_aios_reset_date = Column(DateTime(timezone=True), nullable=True)'''
new = '''    aios_actions_today = Column(Integer, nullable=True, default=0)  # only used by plans with an AIOS daily cap (e.g. pro_account)
    last_aios_reset_date = Column(DateTime(timezone=True), nullable=True)
    aios_credits_remaining = Column(Integer, nullable=True)  # only set for plans with a SEPARATE AIOS pool - see PLAN_AIOS_CREDIT_LIMITS. Plans without one (e.g. pro_account) draw AIOS cost from credits_remaining instead.'''
assert s.count(old) == 1, "models.py: anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("models.py patched")


# --- 2. services/db.py: migration ---
p = "services/db.py"
s = open(p).read()
old = '        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS last_aios_reset_date TIMESTAMPTZ"))'
new = old + '\n        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS aios_credits_remaining INTEGER"))'
assert s.count(old) == 1, "db.py: anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("db.py patched")


# --- 3. services/license_service.py: more_context_account plan + dual credit pools ---
p = "services/license_service.py"
s = open(p).read()

old_valid = 'VALID_PLANS = ["free", "pro", "pro_account", "more_context", "team"]'
new_valid = 'VALID_PLANS = ["free", "pro", "pro_account", "more_context", "more_context_account", "team"]'
assert s.count(old_valid) == 1, "VALID_PLANS anchor not found/not unique"
s = s.replace(old_valid, new_valid, 1)

old_block = '''PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "pro_account": 400, "more_context": 600}
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

new_block = '''PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "pro_account": 400, "more_context": 600, "more_context_account": 1000}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt, Import, or AIOS - same cost either way. Credits do not auto-refill.

# Provider restriction per plan. A plan not listed here has no
# restriction at all (existing behavior, unchanged) - call_llm falls
# back to its normal LLM_PROVIDER env-based order. Only listed plans
# get their provider order filtered. pro_account is intentionally
# absent here - it allows all configured providers.
PLAN_PROVIDERS = {"free": ["gemini"], "more_context": ["anthropic", "openai"], "more_context_account": ["anthropic", "openai"]}

# Plans with a daily AIOS action cap, separate from and in addition to
# their credit pool - an AIOS action costs credits AND counts against
# this daily count. A plan not listed here has no AIOS-specific daily
# cap (though AIOS may still be blocked entirely for that plan - see
# _require_aios_access in main.py). Configurable per plan, not a fixed
# global value.
PLAN_AIOS_DAILY_LIMIT = {"pro_account": 30, "more_context_account": 60}

# Plans with a SEPARATE AIOS credit pool, independent of their general
# PLAN_CREDIT_LIMITS pool - so heavy AIOS usage can never eat into
# Import/Quick Prompt budget on these plans. A plan with an AIOS daily
# cap (PLAN_AIOS_DAILY_LIMIT above) but NO entry here still costs
# credits on AIOS actions - just drawn from the single general pool
# instead (e.g. pro_account).
PLAN_AIOS_CREDIT_LIMITS = {"more_context_account": 500}'''

assert s.count(old_block) == 1, "PLAN_CREDIT_LIMITS block anchor not found/not unique"
s = s.replace(old_block, new_block, 1)

# create_license_after_payment: initialize aios_credits_remaining too
old_create = '''    license = License(
        license_key=key,
        user_id=user_id,
        plan=plan,
        status="active",
        installation_id=installation_id if user_id is None else None,
        ip_hash=ip_hash if user_id is None else None,
        credits_remaining=PLAN_CREDIT_LIMITS.get(plan),
    )'''
new_create = '''    license = License(
        license_key=key,
        user_id=user_id,
        plan=plan,
        status="active",
        installation_id=installation_id if user_id is None else None,
        ip_hash=ip_hash if user_id is None else None,
        credits_remaining=PLAN_CREDIT_LIMITS.get(plan),
        aios_credits_remaining=PLAN_AIOS_CREDIT_LIMITS.get(plan),
    )'''
assert s.count(old_create) == 1, "create_license_after_payment License(...) anchor not found/not unique"
s = s.replace(old_create, new_create, 1)

# check_and_reserve_aios_action: branch on separate AIOS pool vs shared pool
old_reserve = '''def check_and_reserve_aios_action(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
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
    return _serialize(lic)'''

new_reserve = '''def check_and_reserve_aios_action(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
    """Call before an AIOS action (Tell AIOS or AIOS Quick Prompt) on a
    plan with a daily AIOS cap. Raises LicenseError if blocked, otherwise
    atomically reserves the credits AND increments the daily AIOS count.
    Caller must follow with commit_credits() on success or
    release_aios_credits() on failure - the daily count is NOT released
    on failure, mirroring free_license_service's failed-attempt handling
    (a failed attempt still counts, only the credit is refunded).

    Draws from the plan's dedicated AIOS pool (aios_credits_remaining)
    if it has one per PLAN_AIOS_CREDIT_LIMITS, otherwise falls back to
    the shared general pool (credits_remaining) - e.g. pro_account has
    a daily cap but no separate pool, more_context_account has both."""
    lic = _lock_license_by_id(db, license_id)

    if lic.status != "active":
        db.rollback()
        raise LicenseError("This license is not active.")

    _maybe_reset_aios_daily_count(lic)

    daily_limit = PLAN_AIOS_DAILY_LIMIT.get(lic.plan)
    if daily_limit is not None and (lic.aios_actions_today or 0) >= daily_limit:
        db.rollback()
        raise LicenseError("You've reached your daily AIOS usage limit.")

    has_dedicated_pool = lic.plan in PLAN_AIOS_CREDIT_LIMITS
    balance = lic.aios_credits_remaining if has_dedicated_pool else lic.credits_remaining

    if balance is None:
        db.rollback()
        raise LicenseError("This plan doesn't include metered usage.")

    if balance < cost:
        db.rollback()
        raise LicenseError("You've reached your usage limit.")

    if has_dedicated_pool:
        lic.aios_credits_remaining -= cost
    else:
        lic.credits_remaining -= cost
    lic.aios_actions_today = (lic.aios_actions_today or 0) + 1
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def release_aios_credits(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
    """Call on AIOS action failure. Refunds into whichever pool the
    reservation was drawn from - the plan's dedicated AIOS pool if it
    has one, otherwise the shared general pool. Never use release_credits()
    for an AIOS failure, or a more_context_account refund would land in
    the wrong pool."""
    lic = _lock_license_by_id(db, license_id)
    if lic.plan in PLAN_AIOS_CREDIT_LIMITS:
        lic.aios_credits_remaining = (lic.aios_credits_remaining or 0) + cost
    else:
        lic.credits_remaining = (lic.credits_remaining or 0) + cost
    db.commit()
    db.refresh(lic)
    return _serialize(lic)'''

assert s.count(old_reserve) == 1, "check_and_reserve_aios_action anchor not found/not unique"
s = s.replace(old_reserve, new_reserve, 1)

open(p, "w").write(s)
print("license_service.py patched")


# --- 4. main.py: use release_aios_credits (not release_credits) in the AIOS failure paths ---
p = "main.py"
s = open(p).read()

# There are two identical blocks (aios_tell and aios_quick_prompt) - both need this fix
old_release = '''            if is_aios_metered and reserved:
                release_db = get_db_session()
                try:
                    license_service.release_credits(release_db, license_id)
                finally:
                    release_db.close()
            raise'''
new_release = '''            if is_aios_metered and reserved:
                release_db = get_db_session()
                try:
                    license_service.release_aios_credits(release_db, license_id)
                finally:
                    release_db.close()
            raise'''
count = s.count(old_release)
assert count == 2, f"main.py: expected 2 AIOS release anchors, found {count}"
s = s.replace(old_release, new_release)  # replace all occurrences

open(p, "w").write(s)
print(f"main.py patched ({count} AIOS release call sites fixed)")
