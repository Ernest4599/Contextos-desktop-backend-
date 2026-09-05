"""
Admin-only access control. Completely separate from the license/auth
path used by Import and Quick Prompt (services/access_control.py) -
there is no anonymous or license-key path into anything admin-related.

Deliberately does not trust any role/admin claim from the JWT itself:
the token only proves *who* the user is (via decode_session_token,
signature-verified). Whether that user is currently an admin is looked
up fresh from the database on every request, so revoking admin access
takes effect immediately without needing to wait for tokens to expire.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from services.auth_service import decode_session_token, AuthError
from services.db import get_db_session
from services.models import User


def require_admin(authorization: str = Header(default="")) -> int:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not signed in")

    token = authorization[len("Bearer "):]
    try:
        payload = decode_session_token(token)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=e.message)

    user_id = int(payload["sub"])

    db = get_db_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()

    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user_id
