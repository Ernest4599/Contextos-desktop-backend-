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

VALID_PLANS = ["free", "pro", "team"]

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
PLAN_CREDIT_LIMITS = {"pro": 300}
CREDIT_COST_PER_ACTION = 5  # Quick Prompt or Import - same cost either way. Credits do not auto-refill.


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
    )
    db.add(license)
    db.commit()
    db.refresh(license)

    return _serialize(license)


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
