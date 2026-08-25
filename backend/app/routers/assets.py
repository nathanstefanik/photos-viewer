"""Asset listing/metadata, media bytes (thumbnail/original/download/video), and library statistics."""

from __future__ import annotations

from io import BytesIO

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import require_token
from ..deps import get_client, validate_uuid
from ..proxy import stream_upstream
from ..schemas import PaginatedResponse
from ..scope import album_ids_for_search, ensure_asset_in_scope
from ..tokens import TokenRecord

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

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
    size: str = Query("thumbnail", pattern=r"^(thumbnail|preview)$"),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        response = await client.get(f"/api/assets/{asset_id}/thumbnail", params={"size": size})
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "image/jpeg")

        if PIL_AVAILABLE and len(content) > 5 * 1024 * 1024:
            try:
                img = Image.open(BytesIO(content))
                if img.mode == "RGBA" and "jpeg" in content_type.lower():
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    rgb_img.paste(img, mask=img.split()[3])
                    img = rgb_img
                output = BytesIO()
                for quality in [85, 75, 65, 55]:
                    output.seek(0)
                    output.truncate(0)
                    img.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= 5 * 1024 * 1024:
                        break
                content = output.getvalue()
                content_type = "image/jpeg"
            except Exception as e:
                print(f"Warning: Failed to compress image {asset_id}: {e}")

        return StreamingResponse(
            iter([content]),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Thumbnail not found")


@router.get("/assets/{asset_id}/original")
async def get_asset_original(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        req = client.build_request("GET", f"/api/assets/{asset_id}/original")
        response = await client.send(req, stream=True)
        response.raise_for_status()
        return stream_upstream(response, headers={"Cache-Control": "private, max-age=3600"})
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@router.get("/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        meta_resp = await client.get(f"/api/assets/{asset_id}")
        meta_resp.raise_for_status()
        if meta_resp.json().get("type") == "VIDEO":
            raise HTTPException(status_code=403, detail="Video downloads are disabled")

        req = client.build_request("GET", f"/api/assets/{asset_id}/original")
        response = await client.send(req, stream=True)
        response.raise_for_status()

        filename = None
        cd = response.headers.get("content-disposition")
        if cd and "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"')
        if not filename:
            filename = f"{asset_id}.bin"

        return stream_upstream(
            response,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "private, max-age=3600",
            },
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
