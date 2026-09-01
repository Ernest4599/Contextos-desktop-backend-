"""
Security headers + HTTPS enforcement middleware.

Purely additive: does not touch auth, DB, or any existing route logic.
Adds baseline security headers to every response and redirects to HTTPS
in production.

Render terminates TLS at its edge and forwards plain HTTP internally,
setting the `X-Forwarded-Proto` header to tell us what the original
scheme was. We check that header rather than the raw request scheme -
checking the raw scheme behind a proxy causes an infinite redirect loop,
since it's always "http" from the app's point of view.

Locally (no proxy in front of uvicorn), `X-Forwarded-Proto` is absent,
so redirect logic is skipped entirely and only headers are added.

CSP note: actual API routes return JSON only, so they get a locked-down
`default-src 'none'` policy. FastAPI's built-in docs (/docs, /redoc,
/openapi.json) render an HTML page that loads JS/CSS from a CDN - a
'none' policy blocks that entirely and renders a blank page - so those
paths get a relaxed policy that still blocks everything except the
specific CDN Swagger/ReDoc need.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self';"
)

API_CSP = "default-src 'none'; frame-ancestors 'none'"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        forwarded_proto = request.headers.get("x-forwarded-proto")

        # Only enforce HTTPS when we can positively confirm we're behind
        # a proxy that terminated plain HTTP. Absent header = local dev.
        if forwarded_proto == "http":
            https_url = request.url.replace(scheme="https")
            return RedirectResponse(url=str(https_url), status_code=308)

        response = await call_next(request)

        is_docs_path = any(request.url.path.startswith(p) for p in DOCS_PATHS)

        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = DOCS_CSP if is_docs_path else API_CSP
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response
