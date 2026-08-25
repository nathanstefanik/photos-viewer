import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import (
    SESSION_COOKIE,
    client_ip,
    current_token_id,
    is_trusted_proxy,
    make_session_value,
    parse_session_value,
    require_session,
    require_token,
    resolve_token,
    session_cookie_max_age,
    unauthenticated_response,
)
from app.config import settings
from app.tokens import TokenRecord, TokenStore


class FakeRequest:
    def __init__(self, cookies=None, headers=None, client_host="1.2.3.4", token_store=None, path="/api/x"):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host)
        self.app = SimpleNamespace(state=SimpleNamespace(token_store=token_store))
        self.url = SimpleNamespace(path=path)


def make_record(**overrides):
    fields = dict(
        id="id1",
        label="label",
        created_at=0.0,
        last_used_at=None,
        revoked_at=None,
        expires_at=None,
        album_ids=None,
    )
    fields.update(overrides)
    return TokenRecord(**fields)


def test_make_and_parse_session_value_roundtrip():
    value = make_session_value("tok123")
    assert parse_session_value(value) == "tok123"


def test_parse_session_value_rejects_tampered_signature():
    value = make_session_value("tok123")
    payload_b64, sig = value.rsplit(".", 1)
    tampered = f"{payload_b64}.{'0' * len(sig)}"
    assert parse_session_value(tampered) is None


def test_parse_session_value_rejects_garbage():
    assert parse_session_value("not-a-valid-value") is None
    assert parse_session_value("") is None


def test_parse_session_value_rejects_expired_payload(monkeypatch):
    import app.auth as auth_module

    monkeypatch.setattr(auth_module, "SESSION_MAX_AGE", 1)
    value = make_session_value("tok123")
    time.sleep(1.1)
    assert parse_session_value(value) is None


def test_session_cookie_max_age_never_expiring():
    assert session_cookie_max_age(make_record()) == 60 * 60 * 24 * 7


def test_session_cookie_max_age_capped_by_token_expiry():
    record = make_record(expires_at=time.time() + 10)
    age = session_cookie_max_age(record)
    assert 0 < age <= 10


def test_session_cookie_max_age_already_expired_token():
    record = make_record(expires_at=time.time() - 10)
    assert session_cookie_max_age(record) == 0


def test_client_ip_prefers_xff_when_peer_is_trusted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trust_x_forwarded_for", True)
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    req = FakeRequest(headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"}, client_host="127.0.0.1")
    assert client_ip(req) == "9.9.9.9"


def test_client_ip_falls_back_to_real_ip_header_when_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trust_x_forwarded_for", True)
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    req = FakeRequest(headers={"x-real-ip": "8.8.8.8"}, client_host="127.0.0.1")
    assert client_ip(req) == "8.8.8.8"


def test_client_ip_ignores_xff_when_trust_disabled(monkeypatch):
    monkeypatch.setattr(settings, "trust_x_forwarded_for", False)
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    req = FakeRequest(headers={"x-forwarded-for": "9.9.9.9"}, client_host="127.0.0.1")
    assert client_ip(req) == "127.0.0.1"


def test_client_ip_ignores_xff_from_untrusted_peer(monkeypatch):
    """The core of the fix: trust_x_forwarded_for=True alone must not be enough.

    Without the peer matching trusted_proxy_ips, X-Forwarded-For is just a
    header an attacker connecting directly can set to whatever they like —
    honoring it here would let them bypass IP-based rate limiting for free.
    """
    monkeypatch.setattr(settings, "trust_x_forwarded_for", True)
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.1"])  # doesn't match peer
    req = FakeRequest(headers={"x-forwarded-for": "9.9.9.9"}, client_host="6.6.6.6")
    assert client_ip(req) == "6.6.6.6"


def test_client_ip_no_trusted_proxies_configured(monkeypatch):
    monkeypatch.setattr(settings, "trust_x_forwarded_for", True)
    monkeypatch.setattr(settings, "trusted_proxy_ips", [])
    req = FakeRequest(headers={"x-forwarded-for": "9.9.9.9"}, client_host="127.0.0.1")
    assert client_ip(req) == "127.0.0.1"


def test_is_trusted_proxy_matches_exact_ip(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["172.18.0.1"])
    assert is_trusted_proxy("172.18.0.1")
    assert not is_trusted_proxy("172.18.0.2")


def test_is_trusted_proxy_matches_cidr(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["172.18.0.0/16"])
    assert is_trusted_proxy("172.18.5.5")
    assert not is_trusted_proxy("172.19.0.1")


def test_is_trusted_proxy_rejects_garbage_and_empty(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["172.18.0.1"])
    assert not is_trusted_proxy("not-an-ip")
    assert not is_trusted_proxy(None)
    monkeypatch.setattr(settings, "trusted_proxy_ips", [])
    assert not is_trusted_proxy("172.18.0.1")


def test_resolve_token_none_without_cookie():
    req = FakeRequest()
    assert resolve_token(req) is None
    assert current_token_id(req) is None


def test_resolve_token_valid(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"), settings.session_secret)
    record, _raw = store.issue("friend")
    req = FakeRequest(cookies={SESSION_COOKIE: make_session_value(record.id)}, token_store=store)

    resolved = resolve_token(req)
    assert resolved is not None
    assert resolved.id == record.id
    assert current_token_id(req) == record.id


def test_resolve_token_revoked(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"), settings.session_secret)
    record, _raw = store.issue("friend")
    store.revoke(record.id)
    req = FakeRequest(cookies={SESSION_COOKIE: make_session_value(record.id)}, token_store=store)
    assert resolve_token(req) is None


def test_resolve_token_unknown_token_id(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"), settings.session_secret)
    req = FakeRequest(cookies={SESSION_COOKIE: make_session_value("nonexistent")}, token_store=store)
    assert resolve_token(req) is None


async def test_require_session_raises_without_cookie():
    with pytest.raises(HTTPException) as exc:
        await require_session(FakeRequest())
    assert exc.value.status_code == 401


async def test_require_session_touches_last_used(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"), settings.session_secret)
    record, _raw = store.issue("friend")
    req = FakeRequest(cookies={SESSION_COOKIE: make_session_value(record.id)}, token_store=store)

    await require_session(req)
    assert store.get(record.id).last_used_at is not None


async def test_require_token_returns_record(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"), settings.session_secret)
    record, _raw = store.issue("friend")
    req = FakeRequest(cookies={SESSION_COOKIE: make_session_value(record.id)}, token_store=store)

    resolved = await require_token(req)
    assert resolved.id == record.id


async def test_require_token_raises_without_cookie():
    with pytest.raises(HTTPException) as exc:
        await require_token(FakeRequest())
    assert exc.value.status_code == 401


def test_unauthenticated_response_api_path_returns_401_json():
    resp = unauthenticated_response(FakeRequest(path="/api/whatever"))
    assert resp.status_code == 401


def test_unauthenticated_response_page_path_redirects_to_gate():
    resp = unauthenticated_response(FakeRequest(path="/"))
    assert resp.status_code == 302
    assert resp.headers["location"] == "/gate"
