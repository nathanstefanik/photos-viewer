"""Read-only album listing, scoped to the token's album allowlist.

Asset lists within an album are fetched via POST /api/search with albumId,
not returned here — keeps this endpoint's response small and reuses the
existing pagination/scope-checking path instead of a second one.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_token
from ..cache import cache_manager
from ..config import settings
from ..deps import get_client, validate_uuid
from ..tokens import TokenRecord

router = APIRouter(prefix="/api")


def _visible(album: dict, allowed: set) -> bool:
    return allowed is None or album.get("id") in allowed


@router.get("/albums")
async def get_albums(
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    allowed = set(token.album_ids) if token.is_scoped else None
    if allowed is not None and not allowed:
        return {"albums": []}

    cache_key = f"albums:{','.join(sorted(allowed)) if allowed is not None else 'all'}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached

    try:
        response = await client.get("/api/albums")
        response.raise_for_status()
        data = response.json()
        albums = data if isinstance(data, list) else data.get("albums", [])
        visible = [a for a in albums if isinstance(a, dict) and _visible(a, allowed)]
        visible.sort(key=lambda a: (a.get("albumName") or "").lower())
        result = {"albums": visible}
        cache_manager.set(cache_key, result, ttl=settings.cache_ttl_people)
        return result
    except httpx.HTTPStatusError as e:
        return {"albums": [], "error": f"Failed to get albums: {e.response.status_code}"}
    except Exception as e:
        return {"albums": [], "error": f"Failed to get albums: {str(e)}"}


@router.get("/albums/{album_id}")
async def get_album(
    album_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(album_id, "album_id")
    if token.is_scoped and album_id not in (token.album_ids or []):
        raise HTTPException(status_code=404, detail="Album not found")
    try:
        response = await client.get(f"/api/albums/{album_id}")
        response.raise_for_status()
        album = response.json()
        # The asset list belongs to /api/search?albumId=..., not here.
        album.pop("assets", None)
        return album
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Album not found")
