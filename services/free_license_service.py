"""
Free-tier (no-account) license engine. Anonymous, device-scoped via an
installation_id issued by the client and stored in platform secure
storage - see FreeTierLicense in models.py.

This is intentionally separate from license_service.py, which covers
the account-linked (paid) path. Nothing in this file touches that one.

All quota state is server-owned. The client may cache values for
display, but every check/decrement here re-reads from the DB inside
a locked transaction - the client's cached numbers are never trusted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from services.models import FreeTierLicense

DAILY_CREDIT_LIMIT = 50
CREDIT_COST_PER_IMPORT = 10
DAILY_IMPORT_LIMIT = 5
DAILY_FAILED_ATTEMPT_LIMIT = 15

# Simple per-IP creation guard. If you later need something stronger
# (e.g. a sliding window), swap the implementation - callers of
# get_or_create_free_license don't need to change.
MAX_NEW_LICENSES_PER_IP_PER_DAY = 3


class FreeLicenseError(Exception):
    def __init__(self, message: str, code: str = "free_license_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _serialize(lic: FreeTierLicense) -> Dict[str, Any]:
    return {
        "installation_id": lic.installation_id,
        "status": lic.status,
        "credits_remaining": lic.credits_remaining,
        "imports_used_today": lic.imports_used_today,
        "daily_import_limit": DAILY_IMPORT_LIMIT,
        "daily_credit_limit": DAILY_CREDIT_LIMIT,
    }


def _maybe_reset(lic: FreeTierLicense) -> None:
    """Resets daily counters if we've crossed into a new UTC day since
    last_reset_date. Caller must hold the row lock already."""
    now = datetime.now(timezone.utc)
    if lic.last_reset_date is None or lic.last_reset_date.date() != now.date():
        lic.credits_remaining = DAILY_CREDIT_LIMIT
        lic.imports_used_today = 0
        lic.failed_attempts_today = 0
        lic.last_reset_date = now


def get_or_create_free_license(
    db: Session, installation_id: str, ip_hash: str | None = None
) -> Dict[str, Any]:
    lic = (
        db.query(FreeTierLicense)
        .filter(FreeTierLicense.installation_id == installation_id)
        .first()
    )
    if lic:
        _maybe_reset(lic)
        db.commit()
        return _serialize(lic)

    if ip_hash:
        recent_count = (
            db.query(FreeTierLicense)
            .filter(FreeTierLicense.ip_hash == ip_hash)
            .filter(FreeTierLicense.created_at >= _start_of_today())
            .count()
        )
        if recent_count >= MAX_NEW_LICENSES_PER_IP_PER_DAY:
            raise FreeLicenseError(
                "Too many free licenses created from this network today.",
                code="rate_limited",
            )

    lic = FreeTierLicense(
        installation_id=installation_id,
        ip_hash=ip_hash,
        status="active",
        credits_remaining=DAILY_CREDIT_LIMIT,
        imports_used_today=0,
        failed_attempts_today=0,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def _start_of_today() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _lock_license(db: Session, installation_id: str) -> FreeTierLicense:
    """Row-level lock so concurrent import requests for the same
    installation can't both pass the check before either commits."""
    lic = (
        db.query(FreeTierLicense)
        .filter(FreeTierLicense.installation_id == installation_id)
        .with_for_update()
        .first()
    )
    if not lic:
        raise FreeLicenseError("No free license found for this installation.", code="not_found")
    return lic


def check_and_reserve_import(db: Session, installation_id: str) -> Dict[str, Any]:
    """Call this before starting an import. Raises if blocked, otherwise
    reserves the credit/import slot atomically and returns state.
    Caller must follow with commit_import() on success or
    release_import() on failure - see docstrings below."""
    lic = _lock_license(db, installation_id)
    _maybe_reset(lic)

    if lic.status != "active":
        db.rollback()
        raise FreeLicenseError("This free license is not active.", code="inactive")

    if lic.failed_attempts_today >= DAILY_FAILED_ATTEMPT_LIMIT:
        db.rollback()
        raise FreeLicenseError(
            "Too many failed attempts today. Try again tomorrow.",
            code="failed_attempt_limit",
        )

    if lic.imports_used_today >= DAILY_IMPORT_LIMIT:
        db.rollback()
        raise FreeLicenseError(
            "You've used your free imports for today.",
            code="import_limit",
        )

    if lic.credits_remaining < CREDIT_COST_PER_IMPORT:
        db.rollback()
        raise FreeLicenseError(
            "Not enough credits remaining today.",
            code="credit_limit",
        )

    # Reserve now, inside the same locked transaction - this is what
    # closes the race condition between two parallel import requests.
    lic.credits_remaining -= CREDIT_COST_PER_IMPORT
    lic.imports_used_today += 1
    db.commit()
    db.refresh(lic)
    return _serialize(lic)


def commit_import(db: Session, installation_id: str) -> None:
    """No-op on the counters (already reserved) - kept as an explicit
    call site so success is logged/traceable if you add that later."""
    return None


def release_import(db: Session, installation_id: str) -> Dict[str, Any]:
    """Call on import failure. Refunds the reserved credit/import slot
    and counts the failure against the separate failed-attempt budget,
    so retries can't drain the success-based quota for free."""
    lic = _lock_license(db, installation_id)
    lic.credits_remaining += CREDIT_COST_PER_IMPORT
    lic.imports_used_today -= 1
    lic.failed_attempts_today += 1
    db.commit()
    db.refresh(lic)
    return _serialize(lic)
