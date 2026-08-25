"""Security response headers (CSP and friends), applied to every response.

Everything the frontend loads is same-origin (no CDN scripts/fonts/analytics), so the
policy can be strict. The one exception is /api/docs (Swagger UI, debug-only): it pulls
its assets from a CDN, so the policy is skipped there rather than broken.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from .config import settings

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "media-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

_DOCS_PREFIXES = ("/api/docs", "/openapi.json")


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)

        if not request.url.path.startswith(_DOCS_PREFIXES):
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
            response.headers["X-Frame-Options"] = "DENY"

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )
        if settings.public_base_url.startswith("https://"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response
