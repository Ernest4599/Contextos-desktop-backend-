"""
License model and account-based license flows. Payment is stubbed for
now (create_license_after_payment simulates a successful purchase) -
real Stripe/Paystack integration wires in later without changing this
data model or the account-based lookup/display logic.

Standalone (no-account) licenses and recovery codes are a separate,
later piece - this file covers the account-linked path only.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from services.models import License

VALID_PLANS = ["free", "pro", "pro_account", "more_context", "more_context_account", "team"]

# Fixed, branded product-code prefix - not a secret, always the same for
# every license. Only the trailing digits are randomly generated per key.
LICENSE_KEY_PREFIX = "CTXID457"

# Anonymous (no-account) creation guard - mirrors the same pattern
# already used in free_license_service.py. A signed-in user is exempt:
# these caps only apply when a license is being created with no user_id.
MAX_ANONYMOUS_LICENSES_PER_INSTALLATION = 1
MAX_ANONYMOUS_LICENSES_PER_IP_PER_DAY = 3

# Metered-usage plans. Only plans listed here get credits initialized at
# creation and checked before Quick Prompt / Import; any other plan
# (e.g. "free", "team" until added here) has no credit gate at all -
# either fully blocked (free, via the existing plan == "free" check) or
# fully unlimited once licensed (team, today).
PLAN_CREDIT_LIMITS = {"free": 60, "pro": 300, "pro_account": 400, "more_context": 600, "more_context_account": 1000}
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
PLAN_AIOS_CREDIT_LIMITS = {"more_context_account": 500}


class LicenseError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _generate_license_key() -> str:
    """CTXID457-XXXX-XXXX - fixed branded prefix, two random 4-digit
    segments (cryptographically random) carrying the actual uniqueness."""
    segments = ["".join(secrets.choice(string.digits) for _ in range(4)) for _ in range(2)]
    return f"{LICENSE_KEY_PREFIX}-" + "-".join(segments)


def _lock_license_by_id(db: Session, license_id: int) -> License:
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
        raise LicenseError("You've reached your usage limit.")

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


def _serialize(license: License) -> Dict[str, Any]:
    return {
        "license_id": license.id,
        "license_key": license.license_key,
        "plan": license.plan,
        "status": license.status,
        "created_at": license.created_at.isoformat() if license.created_at else None,
        "expires_at": license.expires_at.isoformat() if license.expires_at else None,
    }


def _start_of_today() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def check_anonymous_creation_allowed(
    db: Session, installation_id: Optional[str], ip_hash: Optional[str]
) -> None:
    """
    Call before creating a license with no user_id (the anonymous path
    through /license/purchase-with-codes). Raises LicenseError if either
    cap is exceeded. A signed-in purchase never calls this at all.
    """
    if installation_id:
        existing_count = (
            db.query(License).filter(License.installation_id == installation_id).count()
        )
        if existing_count >= MAX_ANONYMOUS_LICENSES_PER_INSTALLATION:
            raise LicenseError("You've already created a license on this device.")

    if ip_hash:
        today_count = (
            db.query(License)
            .filter(License.ip_hash == ip_hash, License.created_at >= _start_of_today())
            .count()
        )
        if today_count >= MAX_ANONYMOUS_LICENSES_PER_IP_PER_DAY:
            raise LicenseError("Too many licenses created from this network today.")


def create_license_after_payment(
    db: Session,
    user_id: Optional[int],
    plan: str,
    installation_id: Optional[str] = None,
    ip_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stub for the post-payment step. Real integration will call this from
    a verified payment-provider webhook instead of directly from a route.
    installation_id/ip_hash are only ever set for anonymous (user_id is
    None) creations - see check_anonymous_creation_allowed above.
    """
    if plan not in VALID_PLANS:
        raise LicenseError("Unknown plan")

    key = _generate_license_key()
    # Extremely unlikely, but ensure uniqueness
    while db.query(License).filter(License.license_key == key).first():
        key = _generate_license_key()

    license = License(
        license_key=key,
        user_id=user_id,
        plan=plan,
        status="active",
        installation_id=installation_id if user_id is None else None,
        ip_hash=ip_hash if user_id is None else None,
        credits_remaining=PLAN_CREDIT_LIMITS.get(plan),
        aios_credits_remaining=PLAN_AIOS_CREDIT_LIMITS.get(plan),
    )
    db.add(license)
    db.commit()
    db.refresh(license)

    return _serialize(license)


def _maybe_reset_aios_daily_count(lic: License) -> None:
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
    return _serialize(lic)


def get_or_create_free_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:
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


def mask_license_key(key: str) -> str:
    """CTX-XXXX-XXXX-XXXX -> CTX-****-****-****. Prefix stays visible,
    everything else hidden - the raw key is only ever returned after a
    caller has independently verified identity (see /license/reveal)."""
    parts = key.split("-")
    if len(parts) < 2:
        return key
    return parts[0] + "-" + "-".join("*" * len(p) for p in parts[1:])


def get_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:
    """Display lookup - returns the key MASKED. Used by /license/mine,
    the admin user-detail view, and rotate_code_route's ownership check
    (which only reads license_id, never license_key)."""
    license = (
        db.query(License)
        .filter(License.user_id == user_id, License.status.in_(["active", "expired"]))
        .order_by(License.created_at.desc())
        .first()
    )
    if not license:
        raise LicenseError("No license found for this account")

    result = _serialize(license)
    result["license_key"] = mask_license_key(result["license_key"])
    return result


def get_raw_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:
    """Same lookup as get_license_for_user, but returns the REAL key.
    Only for use after the caller has already verified the person's
    identity via password or recovery code - see /license/reveal."""
    license = (
        db.query(License)
        .filter(License.user_id == user_id, License.status.in_(["active", "expired"]))
        .order_by(License.created_at.desc())
        .first()
    )
    if not license:
        raise LicenseError("No license found for this account")

    return _serialize(license)


def get_license_by_key(db: Session, license_key: str) -> Dict[str, Any]:
    """Used by the standalone (no-account) path once recovery codes are built."""
    license = db.query(License).filter(License.license_key == license_key).first()
    if not license:
        raise LicenseError("License not found")

    return _serialize(license)
