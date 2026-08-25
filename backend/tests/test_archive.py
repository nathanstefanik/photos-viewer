"""Tests for POST /api/download/archive: per-asset scope enforcement before proxying."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.cache import cache_manager
from app.deps import get_client
from app.routers import assets
from app.tokens import TokenRecord

ASSET_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ASSET_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ALBUM_X = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def make_token(album_ids=None):
    return TokenRecord(
        id="tok1",
        label="test",
        created_at=0,
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
        album_ids=album_ids,
    )


def build_app(token, handler):
    app = FastAPI()
    app.include_router(assets.router)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://immich.test")
    app.dependency_overrides[get_client] = lambda: client
    app.dependency_overrides[require_token] = lambda: token
    return app


@pytest.fixture(autouse=True)
def clear_cache():
    cache_manager.clear()
    yield
    cache_manager.clear()


def test_archive_unscoped_forwards_to_immich():
    def handler(request):
        if request.url.path == "/api/download/archive":
            body = json.loads(request.content)
            assert body["assetIds"] == [ASSET_A, ASSET_B]
            return httpx.Response(
                200,
                content=b"PK\x03\x04fakezip",
                headers={"content-type": "application/zip"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    app = build_app(make_token(None), handler)
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": [ASSET_A, ASSET_B]})
    assert resp.status_code == 200
    assert resp.content == b"PK\x03\x04fakezip"
    assert "photos.zip" in resp.headers["content-disposition"]


def test_archive_scoped_allows_when_all_assets_in_scope():
    def handler(request):
        if request.url.path == "/api/albums":
            return httpx.Response(200, json=[{"id": ALBUM_X}])
        if request.url.path == "/api/download/archive":
            return httpx.Response(200, content=b"zip", headers={"content-type": "application/zip"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    token = make_token([ALBUM_X])
    app = build_app(token, handler)
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": [ASSET_A]})
    assert resp.status_code == 200


def test_archive_scoped_denies_if_any_asset_out_of_scope():
    archive_was_called = []

    def handler(request):
        if request.url.path == "/api/albums":
            asset_id = request.url.params.get("assetId")
            albums = [{"id": ALBUM_X}] if asset_id == ASSET_A else []
            return httpx.Response(200, json=albums)
        if request.url.path == "/api/download/archive":
            archive_was_called.append(True)
        raise AssertionError(f"archive must not be requested: {request.url.path}")

    token = make_token([ALBUM_X])
    app = build_app(token, handler)
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": [ASSET_A, ASSET_B]})
    assert resp.status_code == 404
    assert not archive_was_called


def test_archive_rejects_empty_asset_list():
    app = build_app(make_token(None), lambda req: httpx.Response(200))
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": []})
    assert resp.status_code == 422


def test_archive_rejects_invalid_uuid():
    app = build_app(make_token(None), lambda req: httpx.Response(200))
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": ["not-a-uuid"]})
    assert resp.status_code == 422


def test_archive_rejects_too_many_assets():
    app = build_app(make_token(None), lambda req: httpx.Response(200))
    too_many = [ASSET_A] * 501
    resp = TestClient(app).post("/api/download/archive", json={"assetIds": too_many})
    assert resp.status_code == 422
