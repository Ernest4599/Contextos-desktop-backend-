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

from fastapi import Cookie, Header, HTTPException

from services.auth_service import decode_session_token, AuthError
from services.db import get_db_session
from services.models import License


class AccessContext:
    def __init__(self, user_id: Optional[int], license_id: Optional[int], via: str, installation_id: Optional[str] = None, plan: Optional[str] = None):
        self.user_id = user_id
        self.license_id = license_id
        # Plan of the resolved license, when via == "license" (e.g. "free",
        # "pro", "team"). None for via == "session" or via == "free" - a
        # signed-in session is allowed regardless of plan per this file's
        # docstring, and the no-account free tier has no License row at all.
        self.plan = plan
        # "session" = real signed-in Bearer token; "license" = license-key
        # only. A license can be tied to an account (license.user_id set)
        # without the request itself being an authenticated session - e.g.
        # someone bought a plan without ever logging in. Features that must
        # be signed-in-only (like saving Context Packages) should check
        # `via == "session"`, not just whether user_id is present.
        self.via = via
        self.installation_id = installation_id


def require_access(
    authorization: str = Header(default=""),
    x_license_key: str = Header(default="", alias="X-License-Key"),
    contextos_installation_id: str | None = Cookie(default=None),
) -> AccessContext:
    # Signed-in users are always allowed. Also resolve their license
    # (if any) so per-plan credit metering (e.g. Pro) applies to them
    # too - being signed in must never mean unmetered usage on a
    # metered plan, it only means access itself is never blocked here.
    if authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        try:
            payload = decode_session_token(token)
            user_id = int(payload["sub"])

            license_id = None
            plan = None
            db = get_db_session()
            try:
                owned_license = (
                    db.query(License)
                    .filter(License.user_id == user_id, License.status == "active")
                    .order_by(License.created_at.desc())
                    .first()
                )
                if owned_license:
                    license_id = owned_license.id
                    plan = owned_license.plan
            finally:
                db.close()

            return AccessContext(user_id=user_id, license_id=license_id, via="session", installation_id=None, plan=plan)
        except AuthError:
            pass  # invalid/expired token - fall through to license check

    # No valid session - require an active, non-expired license key.
    key = (x_license_key or "").strip()
    if not key:
        # No session, no license key - fall back to the free/no-account
        # tier, identified by installation_id. Quota is NOT checked here -
        # this function only identifies access type; each caller decides
        # whether the free tier is allowed for that specific feature.
        inst_id = (contextos_installation_id or "").strip()
        if inst_id:
            return AccessContext(user_id=None, license_id=None, via="free", installation_id=inst_id)

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

    return AccessContext(user_id=license.user_id, license_id=license.id, via="license", installation_id=None, plan=license.plan)
