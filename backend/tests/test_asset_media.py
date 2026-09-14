"""Original/download fidelity and authorization at the actual HTTP boundary."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.auth import SESSION_COOKIE, install_auth_gate, make_session_value
from app.media_cache import MediaCache
from app.memory_cache import cache_manager
from app.routers.assets import router
from app.tokens import TokenStore

ASSET_ID = "12345678-1234-1234-1234-123456789abc"
JPEG_BYTES = (Path(__file__).parent / "fixtures" / "exported.jpg").read_bytes()


@pytest.fixture
async def media_app(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request.url.path)
        if request.url.path == f"/api/assets/{ASSET_ID}/original":
            return httpx.Response(200, content=JPEG_BYTES, headers={"content-type": "image/jpeg"})
        if request.url.path == f"/api/assets/{ASSET_ID}":
            return httpx.Response(200, json={"type": "IMAGE", "originalFileName": "exported.jpg"})
        if request.url.path == "/api/albums":
            return httpx.Response(200, json=[{"id": "allowed-album"}])
        raise AssertionError(f"Unexpected upstream request: {request.url}")

    app = FastAPI()
    app.include_router(router)
    install_auth_gate(app)
    store = TokenStore(str(tmp_path / "tokens.db"), "fixture-token-secret")
    app.state.token_store = store
    app.state.media_cache = MediaCache(str(tmp_path / "media"), max_bytes=1024 * 1024)
    record, _ = store.issue("photographer")
    cache_manager.clear()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream), base_url="http://immich.test"
    ) as upstream_client:
        app.state.http_client = upstream_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://viewer.test",
            cookies={SESSION_COOKIE: make_session_value(record.id)},
        ) as client:
            yield client, store, record, calls
    cache_manager.clear()


@pytest.mark.parametrize("first", ["original", "download"])
async def test_original_and_download_preserve_export_bytes_cold_and_cached(media_app, first):
    client, _, _, calls = media_app
    second = "download" if first == "original" else "original"
    for endpoint in (first, second, first, second):
        response = await client.get(f"/api/assets/{ASSET_ID}/{endpoint}")
        assert response.status_code == 200
        assert response.content == JPEG_BYTES
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["cache-control"].startswith("private")
        if endpoint == "download":
            assert response.headers["content-disposition"] == 'attachment; filename="exported.jpg"'
    assert calls.count(f"/api/assets/{ASSET_ID}/original") == 1


@pytest.mark.parametrize("endpoint", ["original", "download"])
async def test_revoked_session_cannot_revalidate_cached_original(media_app, endpoint):
    client, store, record, calls = media_app
    url = f"/api/assets/{ASSET_ID}/{endpoint}"
    await client.get(url)
    cached = await client.get(url)
    assert cached.status_code == 200
    etag = cached.headers["etag"]
    revalidated = await client.get(url, headers={"if-none-match": etag})
    assert revalidated.status_code == 304
    store.revoke(record.id)
    previous_calls = len(calls)
    denied = await client.get(url, headers={"if-none-match": etag})
    assert denied.status_code == 401
    assert JPEG_BYTES not in denied.content
    assert len(calls) == previous_calls


@pytest.mark.parametrize("endpoint", ["original", "download"])
async def test_album_scope_checked_before_cached_original_or_304(media_app, endpoint):
    client, store, _, calls = media_app
    url = f"/api/assets/{ASSET_ID}/{endpoint}"
    await client.get(url)
    cached = await client.get(url)
    outside, _ = store.issue("other album", album_ids=["different-album"])
    client.cookies.set(SESSION_COOKIE, make_session_value(outside.id))
    denied = await client.get(url, headers={"if-none-match": cached.headers["etag"]})
    assert denied.status_code == 404
    assert calls[-1] == "/api/albums"
    assert calls.count(f"/api/assets/{ASSET_ID}/original") == 1
    assert JPEG_BYTES not in denied.content
