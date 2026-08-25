"""Tests for GET /api/session: exposes the token's default-view hints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.routers import pages
from app.tokens import TokenRecord


def make_token(person_ids=None, album_ids=None):
    return TokenRecord(
        id="tok1",
        label="test",
        created_at=0,
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
        album_ids=album_ids,
        person_ids=person_ids,
    )


def build_app(token):
    app = FastAPI()
    app.include_router(pages.router)
    app.dependency_overrides[require_token] = lambda: token
    return app


def test_session_reports_person_ids():
    token = make_token(person_ids=["11111111-1111-1111-1111-111111111111"])
    resp = TestClient(build_app(token)).get("/api/session")
    assert resp.status_code == 200
    assert resp.json() == {
        "personIds": ["11111111-1111-1111-1111-111111111111"],
        "albumScoped": False,
    }


def test_session_empty_when_no_person_ids():
    resp = TestClient(build_app(make_token())).get("/api/session")
    assert resp.status_code == 200
    assert resp.json() == {"personIds": [], "albumScoped": False}


def test_session_reports_album_scoped():
    token = make_token(album_ids=["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"])
    resp = TestClient(build_app(token)).get("/api/session")
    assert resp.status_code == 200
    assert resp.json()["albumScoped"] is True
