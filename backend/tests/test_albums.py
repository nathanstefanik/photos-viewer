"""Tests for the albums router: list/detail scoped to the token's album allowlist."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.cache import cache_manager
from app.deps import get_client
from app.routers import albums
from app.tokens import TokenRecord

ALBUM_A = {
    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "albumName": "Beach",
    "assetCount": 3,
    "assets": [1, 2, 3],
}
ALBUM_B = {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "albumName": "Mountains", "assetCount": 5}


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
    app.include_router(albums.router)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://immich.test")
    app.dependency_overrides[get_client] = lambda: client
    app.dependency_overrides[require_token] = lambda: token
    return app


@pytest.fixture(autouse=True)
def clear_cache():
    cache_manager.clear()
    yield
    cache_manager.clear()


def test_get_albums_unscoped_returns_all():
    app = build_app(make_token(None), lambda req: httpx.Response(200, json=[ALBUM_A, ALBUM_B]))
    resp = TestClient(app).get("/api/albums")
    assert resp.status_code == 200
    assert [a["albumName"] for a in resp.json()["albums"]] == ["Beach", "Mountains"]


def test_get_albums_scoped_filters_to_allowed():
    token = make_token([ALBUM_A["id"]])
    app = build_app(token, lambda req: httpx.Response(200, json=[ALBUM_A, ALBUM_B]))
    resp = TestClient(app).get("/api/albums")
    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["albums"]] == [ALBUM_A["id"]]


def test_get_albums_scoped_empty_short_circuits_without_network():
    def handler(request):
        raise AssertionError("empty album scope must not call Immich")

    app = build_app(make_token([]), handler)
    resp = TestClient(app).get("/api/albums")
    assert resp.status_code == 200
    assert resp.json()["albums"] == []


def test_get_album_unscoped_strips_assets():
    app = build_app(make_token(None), lambda req: httpx.Response(200, json=ALBUM_A))
    resp = TestClient(app).get(f"/api/albums/{ALBUM_A['id']}")
    assert resp.status_code == 200
    assert "assets" not in resp.json()


def test_get_album_scoped_allows_matching_id():
    token = make_token([ALBUM_A["id"]])
    app = build_app(token, lambda req: httpx.Response(200, json=ALBUM_A))
    resp = TestClient(app).get(f"/api/albums/{ALBUM_A['id']}")
    assert resp.status_code == 200


def test_get_album_scoped_denies_non_matching_id():
    def handler(request):
        raise AssertionError("out-of-scope album id must not reach Immich")

    token = make_token([ALBUM_A["id"]])
    app = build_app(token, handler)
    resp = TestClient(app).get(f"/api/albums/{ALBUM_B['id']}")
    assert resp.status_code == 404


def test_get_album_rejects_invalid_uuid():
    app = build_app(make_token(None), lambda req: httpx.Response(200, json=ALBUM_A))
    resp = TestClient(app).get("/api/albums/not-a-uuid")
    assert resp.status_code == 400
