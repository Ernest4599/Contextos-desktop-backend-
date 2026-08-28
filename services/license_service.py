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

VALID_PLANS = ["pro", "team"]


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


def get_license_for_user(db: Session, user_id: int) -> Dict[str, Any]:
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
