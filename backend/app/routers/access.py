"""Gate page, access-code redemption, health check, and static HTML assets."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..auth import (
    SESSION_COOKIE,
    client_ip,
    make_session_value,
    require_token,
    session_cookie_max_age,
)
from ..config import settings
from ..deps import STATIC_DIR, get_client
from ..ratelimit import redeem_allowed
from ..tokens import TokenStore, TokenRecord, normalize_token

router = APIRouter()
logger = logging.getLogger("photos_viewer.access")


def _static_file(name: str, *, missing: str | None = None) -> FileResponse:
    path = STATIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(path)


@router.get("/api/health")
async def health_check(client: httpx.AsyncClient = Depends(get_client)):
    try:
        response = await client.get("/api/server/ping")
        immich_status = "connected" if response.status_code == 200 else "error"
    except Exception as e:
        immich_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "immich": immich_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/t/{raw_token}")
async def redeem_token(raw_token: str, request: Request):
    ip = client_ip(request)
    if not redeem_allowed(ip):
        logger.warning("access code redeem rate-limited ip=%s", ip)
        raise HTTPException(status_code=429, detail="Too many attempts; try again shortly")

    store: TokenStore = request.app.state.token_store
    code = normalize_token(raw_token)
    if len(code) != 6:
        logger.warning("access code redeem failed (malformed) ip=%s", ip)
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    record = store.lookup_raw(code)
    if not record or not record.active:
        logger.warning("access code redeem failed (invalid/revoked) ip=%s", ip)
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    cookie_age = session_cookie_max_age(record)
    if cookie_age <= 0:
        logger.warning("access code redeem failed (expired) ip=%s token_id=%s", ip, record.id)
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    store.touch(record.id)
    logger.info("access code redeemed ip=%s token_id=%s", ip, record.id)
    response = RedirectResponse(url="/", status_code=302)
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=make_session_value(record.id),
        max_age=cookie_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/gate")
async def gate_page():
    return _static_file("gate.html", missing="gate page missing")


@router.get("/")
async def index(_token: TokenRecord = Depends(require_token)):
    return _static_file("index.html", missing="frontend missing")


@router.get("/immich-logo.svg")
async def logo():
    return _static_file("immich-logo.svg")


@router.get("/favicon.ico")
async def favicon_ico():
    return _static_file("favicon.ico")


@router.get("/favicon-32.png")
async def favicon_png():
    return _static_file("favicon-32.png")
