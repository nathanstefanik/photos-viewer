"""Shared FastAPI dependencies and small helpers used across routers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
from fastapi import HTTPException, Request, Response

from .config import settings
from .media_cache import MediaCache
from .social_store import SocialStore, new_guest_id
from .validation import UUID_PATTERN

GUEST_COOKIE = "viewer_guest"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

# Docker copies frontend → backend/static; local run uses the repo frontend/.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_STATIC_CANDIDATES = (_BACKEND_ROOT / "static", _BACKEND_ROOT.parent / "frontend")
STATIC_DIR = next((p for p in _STATIC_CANDIDATES if p.is_dir()), _STATIC_CANDIDATES[0])


def validate_uuid(value: str, field_name: str = "id") -> str:
    if not value or not UUID_PATTERN.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: must be a valid UUID")
    return value


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_social(request: Request) -> SocialStore:
    return request.app.state.social_store


def get_media_cache(request: Request) -> MediaCache:
    return request.app.state.media_cache


def _guest_id_from_request(request: Request) -> Optional[str]:
    raw = (request.cookies.get(GUEST_COOKIE) or "").strip()
    if UUID_PATTERN.match(raw):
        return raw.lower()
    return None


def ensure_guest_id(request: Request, response: Response) -> str:
    guest_id = _guest_id_from_request(request)
    if guest_id:
        return guest_id
    guest_id = new_guest_id()
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        key=GUEST_COOKIE,
        value=guest_id,
        max_age=GUEST_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return guest_id


async def resolve_person_name(
    client: httpx.AsyncClient,
    person_id: Optional[str],
) -> Optional[str]:
    if not person_id:
        return None
    validate_uuid(person_id, "person_id")
    try:
        response = await client.get(f"/api/people/{person_id}")
        response.raise_for_status()
        name = (response.json() or {}).get("name")
        if name and str(name).strip():
            return str(name).strip()
    except httpx.HTTPError:
        return None
    return None
