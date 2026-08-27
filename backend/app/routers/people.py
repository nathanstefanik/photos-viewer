"""Immich server info and named-people reference data."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import require_token
from ..cache import cache_manager
from ..config import settings
from ..deps import get_client, validate_uuid
from ..scope import album_ids_for_search, ensure_person_in_scope, person_ids_in_scope
from ..tokens import TokenRecord

router = APIRouter(prefix="/api")


@router.get("/server-info")
async def get_server_info(
    client: httpx.AsyncClient = Depends(get_client),
    _token: TokenRecord = Depends(require_token),
):
    try:
        response = await client.get("/api/server/about")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get server info")


@router.get("/people")
async def get_people(
    client: httpx.AsyncClient = Depends(get_client),
    withHidden: bool = False,
    token: TokenRecord = Depends(require_token),
):
    allowed = await person_ids_in_scope(client, token)
    if allowed is not None and not allowed:
        return {"people": [], "total": 0}

    scoped = album_ids_for_search(token)
    # Must not share people_{withHidden} across tokens — that served the full
    # Immich directory (names + face ids) to album-scoped sessions.
    if scoped is None:
        cache_key = f"people:full:{withHidden}"
        hidden = withHidden
    else:
        cache_key = f"people:albums:{','.join(sorted(scoped))}"
        hidden = True

    cached = cache_manager.get(cache_key)
    if cached:
        return cached

    try:
        response = await client.get("/api/people", params={"withHidden": hidden})
        response.raise_for_status()
        data = response.json()
        people = data.get("people", data) if isinstance(data, dict) else data
        named_people = [p for p in people if p.get("name")]
        if allowed is not None:
            named_people = [p for p in named_people if p.get("id") in allowed]
        named_people.sort(key=lambda x: x.get("name", "").lower())
        result = {"people": named_people, "total": len(named_people)}
        cache_manager.set(cache_key, result, ttl=settings.cache_ttl_people)
        return result
    except httpx.HTTPStatusError as e:
        return {"people": [], "total": 0, "error": f"Failed to get people: {e.response.status_code}"}
    except Exception as e:
        return {"people": [], "total": 0, "error": f"Failed to get people: {str(e)}"}


@router.get("/people/{person_id}")
async def get_person(
    person_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(person_id, "person_id")
    await ensure_person_in_scope(client, person_id, token)
    try:
        response = await client.get(f"/api/people/{person_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Person not found")


@router.get("/people/{person_id}/thumbnail")
async def get_person_thumbnail(
    person_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(person_id, "person_id")
    await ensure_person_in_scope(client, person_id, token)
    try:
        response = await client.get(f"/api/people/{person_id}/thumbnail")
        response.raise_for_status()
        return StreamingResponse(
            iter([response.content]),
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Thumbnail not found")
