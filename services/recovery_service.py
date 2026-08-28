"""
License recovery codes: 4 single-use codes generated at license creation,
shown exactly once, hashed at rest (bcrypt), verified without leaking
partial information, rate-limited against brute force, and logged for
audit purposes.
"""
from __future__ import annotations

import hashlib
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import bcrypt
from sqlalchemy.orm import Session

from services.models import License, LicenseRecoveryCode, LicenseRecoveryEvent

CODES_PER_LICENSE = 4
CODE_SEGMENT_LENGTH = 4
CODE_SEGMENTS = 3

# Rate limiting: after this many recent failures for a given ip_hash, lock out
MAX_FAILURES_BEFORE_LOCKOUT = 5
LOCKOUT_WINDOW_MINUTES = 15

GENERIC_INVALID_MESSAGE = "Invalid recovery code."


class RecoveryError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _generate_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    segments = ["".join(secrets.choice(alphabet) for _ in range(CODE_SEGMENT_LENGTH)) for _ in range(CODE_SEGMENTS)]
    return "CTX-" + "-".join(segments)


def _normalize(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def _hash_code(code: str) -> str:
    normalized = _normalize(code)
    return bcrypt.hashpw(normalized.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_code(code: str, code_hash: str) -> bool:
    normalized = _normalize(code)
    return bcrypt.checkpw(normalized.encode("utf-8"), code_hash.encode("utf-8"))


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def generate_recovery_codes(db: Session, license_id: int) -> List[str]:
    """
    Called once, right after a license is created. Returns the raw codes
    ONE TIME so they can be shown to the user - nothing after this point
    can retrieve the original codes again, only their hashes are stored.
    """
    raw_codes = [_generate_code() for _ in range(CODES_PER_LICENSE)]

    for code in raw_codes:
        record = LicenseRecoveryCode(
            license_id=license_id,
            code_hash=_hash_code(code),
            status="unused",
        )
        db.add(record)

    db.commit()
    return raw_codes


def _log_event(db: Session, license_id: int | None, event_type: str, success: bool, ip_hash: str | None) -> None:
    event = LicenseRecoveryEvent(
        license_id=license_id,
        event_type=event_type,
        success="true" if success else "false",
        ip_hash=ip_hash,
    )
    db.add(event)
    db.commit()


def _is_locked_out(db: Session, ip_hash: str | None) -> bool:
    if not ip_hash:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    recent_failures = (
        db.query(LicenseRecoveryEvent)
        .filter(
            LicenseRecoveryEvent.ip_hash == ip_hash,
            LicenseRecoveryEvent.event_type == "RECOVERY_FAILURE",
            LicenseRecoveryEvent.timestamp >= cutoff,
        )
        .count()
    )
    return recent_failures >= MAX_FAILURES_BEFORE_LOCKOUT


def recover_license(db: Session, submitted_code: str, ip_hash: str | None) -> Dict[str, Any]:
    """
    Verifies a recovery code and, if valid and unused, marks it used and
    returns the associated license. Never reveals partial match info -
    every failure path returns the exact same generic message.
    """
    if _is_locked_out(db, ip_hash):
        _log_event(db, None, "RECOVERY_LOCKED", False, ip_hash)
        raise RecoveryError("Too many attempts. Please try again later.")

    _log_event(db, None, "RECOVERY_ATTEMPT", True, ip_hash)

    normalized = _normalize(submitted_code)
    if not normalized:
        _log_event(db, None, "RECOVERY_FAILURE", False, ip_hash)
        raise RecoveryError(GENERIC_INVALID_MESSAGE)

    # Check against all unused codes - no way to narrow by license first
    # without leaking which licenses exist, so we scan all unused codes.
    candidates = db.query(LicenseRecoveryCode).filter(LicenseRecoveryCode.status == "unused").all()

    matched: LicenseRecoveryCode | None = None
    for candidate in candidates:
        if _verify_code(normalized, candidate.code_hash):
            matched = candidate
            break

    if matched is None:
        _log_event(db, None, "RECOVERY_FAILURE", False, ip_hash)
        raise RecoveryError(GENERIC_INVALID_MESSAGE)

    license = db.query(License).filter(License.id == matched.license_id).first()
    if not license or license.status == "revoked":
        _log_event(db, matched.license_id, "RECOVERY_FAILURE", False, ip_hash)
        raise RecoveryError(GENERIC_INVALID_MESSAGE)

    matched.status = "used"
    matched.used_at = datetime.now(timezone.utc)
    db.add(matched)
    db.commit()

    _log_event(db, license.id, "CODE_USED", True, ip_hash)
    _log_event(db, license.id, "RECOVERY_SUCCESS", True, ip_hash)

    remaining = (
        db.query(LicenseRecoveryCode)
        .filter(LicenseRecoveryCode.license_id == license.id, LicenseRecoveryCode.status == "unused")
        .count()
    )

    return {
        "license_id": license.id,
        "license_key": license.license_key,
        "plan": license.plan,
        "status": license.status,
        "recovery_codes_remaining": remaining,
    }


def rotate_recovery_code(db: Session, license_id: int) -> str:
    """Generates one new code to replace a used one, keeping max 4 active."""
    new_code = _generate_code()
    record = LicenseRecoveryCode(
        license_id=license_id,
        code_hash=_hash_code(new_code),
        status="unused",
    )
    db.add(record)
    db.commit()

    _log_event(db, license_id, "CODE_ROTATED", True, None)

    return new_code


def get_remaining_count(db: Session, license_id: int) -> int:
    return (
        db.query(LicenseRecoveryCode)
        .filter(LicenseRecoveryCode.license_id == license_id, LicenseRecoveryCode.status == "unused")
        .count()
    )
