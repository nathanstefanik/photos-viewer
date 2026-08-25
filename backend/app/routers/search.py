"""Search proxy: text -> CLIP smart search, filters -> metadata search, plus typeahead suggestions."""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_token
from ..cache import cache_manager
from ..config import settings
from ..deps import get_client
from ..schemas import PaginatedResponse, SearchFilters
from ..scope import album_ids_for_search
from ..tokens import TokenRecord

router = APIRouter(prefix="/api")


@router.post("/search")
async def search_assets(
    filters: SearchFilters,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    """Proxy search to Immich.

    Text → CLIP smart search. personIds → metadata person filter.
    Both together → smart search constrained to those people.
    Filter-only → metadata.
    """
    search_payload: dict = {"page": filters.page, "size": filters.size}
    if filters.personIds:
        search_payload["personIds"] = filters.personIds
    if filters.make:
        search_payload["make"] = filters.make
    if filters.model:
        search_payload["model"] = filters.model
    if filters.country:
        search_payload["country"] = filters.country
    if filters.city:
        search_payload["city"] = filters.city
    if filters.state:
        search_payload["state"] = filters.state
    if filters.takenAfter:
        search_payload["takenAfter"] = filters.takenAfter
    if filters.takenBefore:
        search_payload["takenBefore"] = filters.takenBefore
    if filters.type and filters.type != "ALL":
        search_payload["type"] = filters.type

    scoped_albums = album_ids_for_search(token)
    if filters.albumId:
        # Browsing a single album: still must be inside the token's allowlist.
        if scoped_albums is not None and filters.albumId not in scoped_albums:
            raise HTTPException(status_code=404, detail="Album not found")
        search_payload["albumIds"] = [filters.albumId]
    elif scoped_albums is not None:
        if not scoped_albums:
            return PaginatedResponse(
                items=[], total=0, page=filters.page, size=filters.size, hasMore=False
            )
        search_payload["albumIds"] = scoped_albums

    query = (filters.query or "").strip()

    try:
        if query:
            search_payload["query"] = query
            endpoint = "/api/search/smart"
        else:
            endpoint = "/api/search/metadata"

        response = await client.post(endpoint, json=search_payload)
        response.raise_for_status()
        data = response.json()
        assets = data.get("assets", {})
        items = assets.get("items", [])
        total = assets.get("total", assets.get("count", len(items)))

        return PaginatedResponse(
            items=items,
            total=total if isinstance(total, int) else len(items),
            page=filters.page,
            size=filters.size,
            hasMore=len(items) >= filters.size,
        )
    except httpx.HTTPStatusError as e:
        error_detail = f"HTTP {e.response.status_code}"
        try:
            error_data = e.response.json()
            error_detail = error_data.get("message", error_data.get("detail", str(error_data)))
        except Exception:
            try:
                error_detail = e.response.text
            except Exception:
                pass
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


@router.get("/search/suggestions")
async def get_search_suggestions(
    make: Optional[str] = Query(None, max_length=100),
    model: Optional[str] = Query(None, max_length=100),
    country: Optional[str] = Query(None, max_length=100),
    city: Optional[str] = Query(None, max_length=100),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    """Aggregate Immich typeahead suggestions (v3 API requires ?type= per field).

    Pass make=/country= to narrow child lists; model=/city= to resolve the parent.
    """
    cache_key = f"search_suggestions:v2:{make or ''}:{model or ''}:{country or ''}:{city or ''}"
    if token.is_scoped:
        cache_key += f":{','.join(token.album_ids or [])}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached

    type_map = {
        "cameraMake": "camera-make",
        "cameraModel": "camera-model",
        "country": "country",
        "city": "city",
        "state": "state",
    }
    result = {key: [] for key in type_map}

    # Immich getCountries() ignores city; infer parent country from one matching asset
    if city and not country:
        try:
            payload: dict = {"page": 1, "size": 1, "city": city, "withExif": True}
            scoped = album_ids_for_search(token)
            if scoped is not None:
                if not scoped:
                    return result
                payload["albumIds"] = scoped
            response = await client.post("/api/search/metadata", json=payload)
            if response.status_code == 200:
                items = response.json().get("assets", {}).get("items", [])
                if items:
                    exif = items[0].get("exifInfo") or {}
                    inferred = exif.get("country") or items[0].get("country")
                    if inferred:
                        result["country"] = [inferred]
            cache_manager.set(cache_key, result, ttl=settings.cache_ttl_suggestions)
            return result
        except httpx.HTTPError:
            return result

    try:
        for out_key, immich_type in type_map.items():
            params: dict = {"type": immich_type}
            if make and immich_type == "camera-model":
                params["make"] = make
            if model and immich_type == "camera-make":
                params["model"] = model
            if country and immich_type == "city":
                params["country"] = country

            if make and immich_type != "camera-model":
                continue
            if model and not make and immich_type != "camera-make":
                continue
            if country and immich_type != "city":
                continue

            response = await client.get("/api/search/suggestions", params=params)
            if response.status_code != 200:
                continue
            data = response.json()
            if isinstance(data, list):
                result[out_key] = [x for x in data if x]
            elif isinstance(data, dict):
                result[out_key] = data.get(out_key) or data.get(immich_type) or []
        cache_manager.set(cache_key, result, ttl=settings.cache_ttl_suggestions)
        return result
    except httpx.HTTPError:
        return result
