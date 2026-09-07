# --- 1. services/models.py: credits_remaining on License ---
p = "services/models.py"
s = open(p).read()
old = '''    installation_id = Column(String, index=True, nullable=True)  # anonymous creation only
    ip_hash = Column(String, nullable=True)  # anonymous creation only'''
new = '''    installation_id = Column(String, index=True, nullable=True)  # anonymous creation only
    ip_hash = Column(String, nullable=True)  # anonymous creation only
    credits_remaining = Column(Integer, nullable=True)  # only set for metered plans (pro) - see PLAN_CREDIT_LIMITS'''
assert s.count(old) == 1, "models.py: License columns anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("models.py patched")

# --- 2. services/db.py: migration ---
p = "services/db.py"
s = open(p).read()
old = '        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS ip_hash VARCHAR"))'
new = old + '\n        conn.execute(text("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS credits_remaining INTEGER"))'
assert s.count(old) == 1, "db.py: ip_hash migration anchor not found/not unique"
s = s.replace(old, new, 1)
open(p, "w").write(s)
print("db.py patched")

# --- 3. services/license_service.py: credit reservation functions ---
p = "services/license_service.py"
s = open(p).read()

old_const = '''MAX_ANONYMOUS_LICENSES_PER_INSTALLATION = 1
MAX_ANONYMOUS_LICENSES_PER_IP_PER_DAY = 3'''
new_const = '''MAX_ANONYMOUS_LICENSES_PER_INSTALLATION = 1
MAX_ANONYMOUS_LICENSES_PER_IP_PER_DAY = 3

# Metered-usage plans. Only plans listed here get credits initialized at
# creation and checked before Quick Prompt / Import; any other plan
# (e.g. "free", "team" until added here) has no credit gate at all -
# either fully blocked (free, via the existing plan == "free" check) or
# fully unlimited once licensed (team, today).
PLAN_CREDIT_LIMITS = {"pro": 300}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.'''
assert s.count(old_const) == 1, "license_service.py: constants anchor not found/not unique"
s = s.replace(old_const, new_const, 1)

old_create_call = '''    license = License(
        license_key=key,
        user_id=user_id,
        plan=plan,
        status="active",
        installation_id=installation_id if user_id is None else None,
        ip_hash=ip_hash if user_id is None else None,
    )'''
new_create_call = '''    license = License(
        license_key=key,
        user_id=user_id,
        plan=plan,
        status="active",
        installation_id=installation_id if user_id is None else None,
        ip_hash=ip_hash if user_id is None else None,
        credits_remaining=PLAN_CREDIT_LIMITS.get(plan),
    )'''
assert s.count(old_create_call) == 1, "license_service.py: License(...) construction anchor not found/not unique"
s = s.replace(old_create_call, new_create_call, 1)

old_end_marker = '''def _serialize(license: License) -> Dict[str, Any]:'''
new_end_marker = '''def _lock_license_by_id(db: Session, license_id: int) -> License:
    """Row-level lock so concurrent Pro requests on the same license
    can't both pass the credit check before either commits."""
    lic = db.query(License).filter(License.id == license_id).with_for_update().first()
    if not lic:
        raise LicenseError("License not found")
    return lic


def check_and_reserve_credits(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
    """Call before a Pro-gated action (Quick Prompt or Import). Raises
    LicenseError if blocked, otherwise atomically reserves the credits
    and returns the updated license state. Caller must follow with
    commit_credits() on success or release_credits() on failure -
    mirrors free_license_service.py's reserve/commit/release pattern."""
    lic = _lock_license_by_id(db, license_id)

    if lic.status != "active":
        db.rollback()
        raise LicenseError("This license is not active.")

    if lic.credits_remaining is None:
        db.rollback()
        raise LicenseError("This plan doesn't include metered usage.")

    if lic.credits_remaining < cost:
        db.rollback()
        raise LicenseError("You've reached your Pro usage limit.")

    lic.credits_remaining -= cost
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def commit_credits(db: Session, license_id: int) -> None:
    """No-op on the counters (already reserved) - kept as an explicit
    call site so success is logged/traceable if you add that later."""
    return None


def release_credits(db: Session, license_id: int, cost: int = CREDIT_COST_PER_ACTION) -> Dict[str, Any]:
    """Call on action failure. Refunds the reserved credits so a
    server/API failure never costs the user their Pro usage."""
    lic = _lock_license_by_id(db, license_id)
    lic.credits_remaining = (lic.credits_remaining or 0) + cost
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def _serialize(license: License) -> Dict[str, Any]:'''
assert s.count(old_end_marker) == 1, "license_service.py: _serialize anchor not found/not unique"
s = s.replace(old_end_marker, new_end_marker, 1)

open(p, "w").write(s)
print("license_service.py patched")
