import time

import pytest

from app.tokens import TokenStore, _legacy_hash_token, format_token, hash_token, normalize_token


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.db"), secret="unit-test-secret")


def test_normalize_token():
    assert normalize_token("abc-def") == "ABCDEF"
    assert normalize_token("ABC DEF") == "ABCDEF"
    assert normalize_token("abc.def!") == "ABCDEF"


def test_format_token():
    assert format_token("abcdef") == "ABC-DEF"
    assert format_token("abc-def") == "ABC-DEF"


def test_issue_and_lookup_roundtrip(store):
    record, raw = store.issue("friend")
    assert record.label == "friend"
    assert record.album_ids is None
    assert record.active

    looked_up = store.lookup_raw(raw)
    assert looked_up is not None
    assert looked_up.id == record.id


def test_issue_defaults_label_when_blank(store):
    record, _raw = store.issue("   ")
    assert record.label == "friend"


def test_lookup_wrong_code_fails(store):
    store.issue("friend")
    assert store.lookup_raw("ZZZZZZ") is None


def test_lookup_accepts_separators_and_case(store):
    _record, raw = store.issue("friend")
    formatted = format_token(raw).lower()
    assert store.lookup_raw(formatted) is not None


def test_revoke_disables_lookup(store):
    record, raw = store.issue("friend")
    assert store.revoke(record.id) is True

    looked_up = store.lookup_raw(raw)
    assert looked_up is not None
    assert not looked_up.active

    # revoking again is a no-op
    assert store.revoke(record.id) is False


def test_expiry(store):
    _record, raw = store.issue("friend", expires_at=time.time() - 1)
    looked_up = store.lookup_raw(raw)
    assert looked_up.expired
    assert not looked_up.active


def test_album_scope_roundtrip(store):
    record, _raw = store.issue("friend", album_ids=["11111111-1111-1111-1111-111111111111"])
    assert record.is_scoped

    assert store.set_album_scope(record.id, ["22222222-2222-2222-2222-222222222222"])
    updated = store.get(record.id)
    assert updated.album_ids == ["22222222-2222-2222-2222-222222222222"]

    assert store.set_album_scope(record.id, None)
    updated = store.get(record.id)
    assert not updated.is_scoped


def test_touch_updates_last_used(store):
    record, _raw = store.issue("friend")
    assert store.get(record.id).last_used_at is None
    store.touch(record.id)
    assert store.get(record.id).last_used_at is not None


def test_touch_does_not_revive_revoked_token(store):
    record, _raw = store.issue("friend")
    store.revoke(record.id)
    store.touch(record.id)
    assert store.get(record.id).last_used_at is None


def test_legacy_plain_sha256_hash_upgrades_on_redeem(store):
    """Rows hashed before the HMAC migration must still redeem, and get upgraded in place."""
    raw = "ABCDEF"
    legacy_hash = _legacy_hash_token(raw)
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO tokens (id, label, token_hash, created_at, last_used_at, "
            "revoked_at, expires_at, album_ids) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL)",
            ("legacyid", "legacy", legacy_hash, time.time()),
        )
        conn.commit()

    looked_up = store.lookup_raw(raw)
    assert looked_up is not None
    assert looked_up.id == "legacyid"

    with store._connect() as conn:
        row = conn.execute(
            "SELECT token_hash FROM tokens WHERE id = ?", ("legacyid",)
        ).fetchone()
    assert row["token_hash"] == hash_token(raw, store.secret)
    assert row["token_hash"] != legacy_hash


def test_list_orders_newest_first(store):
    store.issue("first")
    time.sleep(0.01)
    store.issue("second")
    rows = store.list()
    assert [r.label for r in rows] == ["second", "first"]


def test_requires_secret(tmp_path):
    with pytest.raises(RuntimeError):
        TokenStore(str(tmp_path / "x.db"), secret="")
