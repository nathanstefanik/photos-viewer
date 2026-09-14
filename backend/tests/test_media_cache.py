import asyncio

import httpx
import pytest
from fastapi import Request

from app.media_cache import MediaCache, asset_original_key


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, asyncio.CancelledError])
async def test_original_can_be_retried_after_upstream_failure(tmp_path, failure):
    cache = MediaCache(str(tmp_path), max_bytes=1024)
    key = asset_original_key("asset-1")
    request = Request({"type": "http", "headers": []})
    unavailable = True

    def upstream(request):
        if unavailable:
            raise failure("Immich unavailable")
        return httpx.Response(200, content=b"photo", headers={"Content-Type": "image/jpeg"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), base_url="http://immich.test"
    ) as client:

        async def fetch():
            return await cache.stream_or_cached(
                key, request, client, "/api/assets/asset-1/original", cache_control="private"
            )

        with pytest.raises(failure):
            await fetch()

        unavailable = False
        response = await asyncio.wait_for(fetch(), timeout=1)
        assert b"".join([chunk async for chunk in response.body_iterator]) == b"photo"
        assert cache.lookup(key).path.read_bytes() == b"photo"
