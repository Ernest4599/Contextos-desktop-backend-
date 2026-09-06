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
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from services.models import License

VALID_PLANS = ["free", "pro", "team"]


class LicenseError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _generate_license_key() -> str:
    """CTX-XXXX-XXXX-XXXX style key, cryptographically random."""
    alphabet = string.ascii_uppercase + string.digits
    segments = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "CTX-" + "-".join(segments)


def _serialize(license: License) -> Dict[str, Any]:
    return {
        "license_id": license.id,
        "license_key": license.license_key,
        "plan": license.plan,
        "status": license.status,
        "created_at": license.created_at.isoformat() if license.created_at else None,
        "expires_at": license.expires_at.isoformat() if license.expires_at else None,
    }


def create_license_after_payment(db: Session, user_id: Optional[int], plan: str) -> Dict[str, Any]:
    """
    Stub for the post-payment step. Real integration will call this from
    a verified payment-provider webhook instead of directly from a route.
    """
    if plan not in VALID_PLANS:
        raise LicenseError("Unknown plan")

    key = _generate_license_key()
    # Extremely unlikely, but ensure uniqueness
    while db.query(License).filter(License.license_key == key).first():
        key = _generate_license_key()

    license = License(license_key=key, user_id=user_id, plan=plan, status="active")
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
