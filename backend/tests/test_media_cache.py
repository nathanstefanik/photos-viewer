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


async def test_abandoned_original_discards_partial_file_and_releases_waiting_request(tmp_path):
    cache = MediaCache(str(tmp_path), max_bytes=1024 * 1024)
    key = asset_original_key("asset-2")
    request = Request({"type": "http", "headers": []})
    first_chunk = b"x" * 65536
    original_bytes = first_chunk + b"end of original"
    started = asyncio.Event()
    closed = asyncio.Event()
    calls = 0

    class SlowOriginal(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            yield first_chunk
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=SlowOriginal(), headers={"content-type": "image/jpeg"})
        return httpx.Response(200, content=original_bytes, headers={"content-type": "image/jpeg"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), base_url="http://immich.test"
    ) as client:

        async def fetch():
            return await cache.stream_or_cached(
                key, request, client, "/api/assets/asset-2/original", cache_control="private"
            )

        leader = await fetch()
        assert await anext(leader.body_iterator) == first_chunk
        assert started.is_set()
        follower = asyncio.create_task(fetch())
        try:
            await asyncio.sleep(0)  # let the follower wait on the unfinished fill
            assert not follower.done()
            await leader.body_iterator.aclose()  # navigation abandons the old transfer
            assert closed.is_set()
            assert cache.lookup(key) is None
            assert list(tmp_path.rglob("*.tmp")) == []
            response = await asyncio.wait_for(follower, timeout=1)
            assert b"".join([chunk async for chunk in response.body_iterator]) == original_bytes
            assert cache.lookup(key).path.read_bytes() == original_bytes
            assert calls == 2
        finally:
            follower.cancel()
            await asyncio.gather(follower, return_exceptions=True)
