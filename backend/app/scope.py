"""Album-scope enforcement for gated access tokens."""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import HTTPException

from .cache import cache_manager
from .tokens import TokenRecord


async def ensure_asset_in_scope(
    client: httpx.AsyncClient,
    asset_id: str,
    token: TokenRecord,
) -> None:
    """404 if the asset is outside the token's album allowlist. Unscoped tokens pass."""
    if not token.is_scoped:
        return
    assert token.album_ids is not None
    if not token.album_ids:
        raise HTTPException(status_code=404, detail="Asset not found")

    cache_key = f"asset_albums:{asset_id}"
    album_ids = cache_manager.get(cache_key)
    if album_ids is None:
        try:
            response = await client.get("/api/albums", params={"assetId": asset_id})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                data = data.get("albums", []) if isinstance(data, dict) else []
            album_ids = [a["id"] for a in data if isinstance(a, dict) and a.get("id")]
        except httpx.HTTPStatusError:
            raise HTTPException(status_code=404, detail="Asset not found")
        cache_manager.set(cache_key, album_ids, ttl=120)

    allowed = set(token.album_ids)
    if not allowed.intersection(album_ids):
        raise HTTPException(status_code=404, detail="Asset not found")


def album_ids_for_search(token: TokenRecord) -> Optional[list[str]]:
    """Album filter to inject into Immich search, or None for unrestricted."""
    if not token.is_scoped:
        return None
    return list(token.album_ids or [])
