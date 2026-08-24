"""Signed session cookies bound to a non-revoked, non-expired access token."""

from __future__ import annotations

import hmac
import hashlib
import json
import base64
import time
from typing import Optional

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

from .config import settings
from .tokens import TokenStore, TokenRecord

SESSION_COOKIE = "viewer_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _sign(payload_b64: str) -> str:
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def make_session_value(token_id: str) -> str:
    payload = json.dumps({"tid": token_id, "iat": int(time.time())}, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_session_value(value: str) -> Optional[str]:
    try:
        payload_b64, sig = value.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        token_id = payload.get("tid")
        iat = int(payload.get("iat", 0))
    except Exception:
        return None
    if not token_id or (time.time() - iat) > SESSION_MAX_AGE:
        return None
    return token_id


def get_token_store(request: Request) -> TokenStore:
    return request.app.state.token_store


def client_ip(request: Request) -> str:
    """Real client IP when behind Caddy; otherwise the direct peer."""
    if settings.trust_x_forwarded_for:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def session_cookie_max_age(record: TokenRecord) -> int:
    age = SESSION_MAX_AGE
    if record.expires_at is not None:
        remaining = int(record.expires_at - time.time())
        if remaining <= 0:
            return 0
        age = min(age, remaining)
    return age


def resolve_token(request: Request) -> Optional[TokenRecord]:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    token_id = parse_session_value(raw)
    if not token_id:
        return None
    store: TokenStore = request.app.state.token_store
    record = store.get(token_id)
    if not record or not record.active:
        return None
    return record


def current_token_id(request: Request) -> Optional[str]:
    record = resolve_token(request)
    return record.id if record else None


async def require_session(request: Request) -> str:
    record = resolve_token(request)
    if not record:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.app.state.token_store.touch(record.id)
    return record.id


async def require_token(request: Request) -> TokenRecord:
    record = resolve_token(request)
    if not record:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.app.state.token_store.touch(record.id)
    return record


def unauthenticated_response(request: Request):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return RedirectResponse(url="/gate", status_code=302)
