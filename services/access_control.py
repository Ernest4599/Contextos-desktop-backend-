"""
Access control for endpoints that non-account users can reach with a
valid license key (Import, Quick Prompt). Signed-in users are always
allowed regardless of license status - this only gates the no-account
path per the standalone license architecture.

Never trusts a client claim of identity or license ownership: a Bearer
token is verified via decode_session_token (signature + expiry checked
server-side), and a license key is looked up and validated against the
database - never taken at face value.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException

from services.auth_service import decode_session_token, AuthError
from services.db import get_db_session
from services.models import License


class AccessContext:
    def __init__(self, user_id: Optional[int], license_id: Optional[int]):
        self.user_id = user_id
        self.license_id = license_id


def require_access(
    authorization: str = Header(default=""),
    x_license_key: str = Header(default="", alias="X-License-Key"),
) -> AccessContext:
    # Signed-in users are always allowed.
    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        try:
            payload = decode_session_token(token)
            return AccessContext(user_id=int(payload["sub"]), license_id=None)
        except AuthError:
            pass  # invalid/expired token - fall through to license check

    # No valid session - require an active, non-expired license key.
    key = (x_license_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=401,
            detail="Sign in or enter a license key to use this feature.",
        )

    db = get_db_session()
    try:
        license = db.query(License).filter(License.license_key == key).first()
    finally:
        db.close()

    if not license or license.status != "active":
        raise HTTPException(status_code=401, detail="Invalid or inactive license key.")

    if license.expires_at and license.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="This license has expired.")

    return AccessContext(user_id=license.user_id, license_id=license.id)
