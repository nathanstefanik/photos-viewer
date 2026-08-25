"""Tests for /api/search's albumId handling: browsing a single album must still
respect the token's album allowlist."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.cache import cache_manager
from app.deps import get_client
from app.routers import search
from app.tokens import TokenRecord

ALLOWED_ALBUM = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_ALBUM = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


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
    app.include_router(search.router)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://immich.test")
    app.dependency_overrides[get_client] = lambda: client
    app.dependency_overrides[require_token] = lambda: token
    return app


@pytest.fixture(autouse=True)
def clear_cache():
    cache_manager.clear()
    yield
    cache_manager.clear()


def test_album_id_unscoped_forwards_as_sole_album_filter():
    import json

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"assets": {"items": [], "total": 0}})

    app = build_app(make_token(None), handler)
    resp = TestClient(app).post("/api/search", json={"albumId": ALLOWED_ALBUM, "page": 1, "size": 50})
    assert resp.status_code == 200
    assert seen["body"]["albumIds"] == [ALLOWED_ALBUM]


def test_album_id_within_scope_allowed():
    def handler(request):
        return httpx.Response(200, json={"assets": {"items": [], "total": 0}})

    token = make_token([ALLOWED_ALBUM])
    app = build_app(token, handler)
    resp = TestClient(app).post("/api/search", json={"albumId": ALLOWED_ALBUM, "page": 1, "size": 50})
    assert resp.status_code == 200


def test_album_id_outside_scope_denied_without_network():
    def handler(request):
        raise AssertionError("out-of-scope albumId must not reach Immich")

    token = make_token([ALLOWED_ALBUM])
    app = build_app(token, handler)
    resp = TestClient(app).post("/api/search", json={"albumId": OTHER_ALBUM, "page": 1, "size": 50})
    assert resp.status_code == 404


def test_album_id_rejects_invalid_uuid():
    app = build_app(make_token(None), lambda req: httpx.Response(200, json={}))
    resp = TestClient(app).post("/api/search", json={"albumId": "not-a-uuid", "page": 1, "size": 50})
    assert resp.status_code == 422
