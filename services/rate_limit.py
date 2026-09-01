"""
Rate limiting configuration.

Uses in-memory storage (fine for a single Render instance; would need
Redis-backed storage if this backend ever runs multiple instances behind
a load balancer, since in-memory counters don't sync across processes).

Keys by authenticated user ID when a valid token is present, falling
back to IP address for anonymous requests. This stops a logged-in user
from dodging limits by rotating IPs, while still protecting unauthenticated
endpoints like signup/login.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from services.auth_service import decode_session_token, AuthError


def _rate_limit_key(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            payload = decode_session_token(token)
            return f"user:{payload['sub']}"
        except AuthError:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key, default_limits=["60/minute"])
