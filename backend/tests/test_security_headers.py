import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.security_headers import CONTENT_SECURITY_POLICY, install_security_headers


@pytest.fixture
def app():
    test_app = FastAPI()

    @test_app.get("/plain")
    async def plain():
        return {"ok": True}

    @test_app.get("/api/docs")
    async def docs():
        return {"docs": True}

    install_security_headers(test_app)
    return test_app


def test_security_headers_present_on_normal_route(app):
    client = TestClient(app)
    resp = client.get("/plain")
    assert resp.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "permissions-policy" in resp.headers


def test_csp_skipped_on_docs_route(app):
    client = TestClient(app)
    resp = client.get("/api/docs")
    assert "content-security-policy" not in resp.headers
    assert "x-frame-options" not in resp.headers
    # Non-CSP hardening headers still apply everywhere
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_hsts_only_set_for_https_public_base_url(app, monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8080")
    resp = client.get("/plain")
    assert "strict-transport-security" not in resp.headers

    monkeypatch.setattr(settings, "public_base_url", "https://photos.example.com")
    resp = client.get("/plain")
    assert "strict-transport-security" in resp.headers
