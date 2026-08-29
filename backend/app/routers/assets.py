"""Asset listing/metadata, media bytes (thumbnail/original/download/video), and library statistics."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_token
from ..deps import get_client, get_media_cache, validate_uuid
from ..media_cache import MediaCache, as_response, asset_original_key, asset_thumb_key
from ..proxy import stream_upstream
from ..schemas import PaginatedResponse
from ..scope import album_ids_for_search, ensure_asset_in_scope
from ..tokens import TokenRecord

router = APIRouter(prefix="/api")


@router.get("/assets")
async def get_assets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    payload: dict = {"page": page, "size": size}
    scoped_albums = album_ids_for_search(token)
    if scoped_albums is not None:
        if not scoped_albums:
            return PaginatedResponse(items=[], total=0, page=page, size=size, hasMore=False)
        payload["albumIds"] = scoped_albums
    try:
        response = await client.post("/api/search/metadata", json=payload)
        response.raise_for_status()
        data = response.json()
        assets = data.get("assets", {})
        items = assets.get("items", [])
        total = assets.get("count", len(items))
        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            hasMore=len(items) >= size,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get assets")


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        response = await client.get(f"/api/assets/{asset_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@router.get("/assets/{asset_id}/thumbnail")
async def get_asset_thumbnail(
    asset_id: str,
    request: Request,
    size: str = Query("thumbnail", pattern=r"^(thumbnail|preview)$"),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    cache: MediaCache = Depends(get_media_cache),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    key = asset_thumb_key(asset_id, size)

    async def filler():
        return await cache.fill_http(
            key, client, f"/api/assets/{asset_id}/thumbnail", {"size": size}
        )

    try:
        cached = await cache.get_or_fill(key, filler)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Thumbnail not found")
    return as_response(cached, request, "private, max-age=86400")


@router.get("/assets/{asset_id}/original")
async def get_asset_original(
    asset_id: str,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    cache: MediaCache = Depends(get_media_cache),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        return await cache.stream_or_cached(
            asset_original_key(asset_id),
            request,
            client,
            f"/api/assets/{asset_id}/original",
            cache_control="private, max-age=86400",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@router.get("/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    cache: MediaCache = Depends(get_media_cache),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        meta_resp = await client.get(f"/api/assets/{asset_id}")
        meta_resp.raise_for_status()
        meta = meta_resp.json()
        if meta.get("type") == "VIDEO":
            raise HTTPException(status_code=403, detail="Video downloads are disabled")

        filename = (meta.get("originalFileName") or f"{asset_id}.bin").replace('"', "")
        return await cache.stream_or_cached(
            asset_original_key(asset_id),
            request,
            client,
            f"/api/assets/{asset_id}/original",
            cache_control="private, max-age=86400",
            extra_headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@router.get("/assets/{asset_id}/video/playback")
async def get_video_playback(
    asset_id: str,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    client_range = request.headers.get("range")
    range_header = client_range if client_range else "bytes=0-"
    try:
        req = client.build_request(
            "GET",
            f"/api/assets/{asset_id}/video/playback",
            headers={"Range": range_header},
        )
        response = await client.send(req, stream=True)
        response.raise_for_status()

        headers = {"Accept-Ranges": response.headers.get("accept-ranges", "bytes")}
        if "content-range" in response.headers:
            headers["Content-Range"] = response.headers["content-range"]

        return stream_upstream(response, default_media_type="video/mp4", headers=headers)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Video not found")


@router.get("/statistics")
async def get_statistics(
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    # Library-wide stats would leak totals under album-scoped tokens
    if token.is_scoped:
        return {"photos": 0, "videos": 0, "usage": 0, "scoped": True}
    try:
        response = await client.get("/api/assets/statistics")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get statistics")
