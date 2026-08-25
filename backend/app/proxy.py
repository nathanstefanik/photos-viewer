"""Shared helper for streaming an upstream Immich response back to the browser."""

from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx
from fastapi.responses import StreamingResponse

CHUNK_SIZE = 65536


async def _iter_body(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_bytes(chunk_size=CHUNK_SIZE):
        yield chunk
    await response.aclose()


def stream_upstream(
    response: httpx.Response,
    *,
    default_media_type: str = "application/octet-stream",
    headers: Optional[dict] = None,
) -> StreamingResponse:
    """Wrap a streamed httpx response (from client.send(..., stream=True)) as a
    FastAPI StreamingResponse: forwards status code, content-type, content-length,
    and any caller-supplied headers (Content-Disposition, Cache-Control, Accept-Ranges, ...).
    """
    out_headers = dict(headers or {})
    if response.headers.get("content-length"):
        out_headers["Content-Length"] = response.headers["content-length"]

    return StreamingResponse(
        _iter_body(response),
        status_code=response.status_code,
        media_type=response.headers.get("content-type", default_media_type),
        headers=out_headers,
    )
