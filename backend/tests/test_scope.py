import httpx
import pytest
from fastapi import HTTPException

from app.memory_cache import cache_manager
from app.scope import album_ids_for_search, ensure_asset_in_scope
from app.tokens import TokenRecord


@pytest.fixture(autouse=True)
def clear_cache():
    cache_manager.clear()
    yield
    cache_manager.clear()


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


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://immich.test")


def test_album_ids_for_search_unscoped():
    assert album_ids_for_search(make_token(None)) is None


def test_album_ids_for_search_scoped():
    ids = ["a1", "a2"]
    assert album_ids_for_search(make_token(ids)) == ids


def test_album_ids_for_search_scoped_empty():
    assert album_ids_for_search(make_token([])) == []


async def test_ensure_asset_in_scope_unscoped_never_calls_immich():
    def handler(request):
        raise AssertionError("unscoped tokens must not hit Immich")

    client = make_client(handler)
    await ensure_asset_in_scope(client, "asset-1", make_token(None))
    await client.aclose()


async def test_ensure_asset_in_scope_empty_scope_denies_without_network():
    def handler(request):
        raise AssertionError("empty album scope must short-circuit before any request")

    client = make_client(handler)
    with pytest.raises(HTTPException) as exc:
        await ensure_asset_in_scope(client, "asset-1", make_token([]))
    assert exc.value.status_code == 404
    await client.aclose()


async def test_ensure_asset_in_scope_allows_matching_album():
    client = make_client(lambda req: httpx.Response(200, json=[{"id": "album-a"}]))
    await ensure_asset_in_scope(client, "asset-2", make_token(["album-a"]))
    await client.aclose()


async def test_ensure_asset_in_scope_denies_non_matching_album():
    client = make_client(lambda req: httpx.Response(200, json=[{"id": "album-x"}]))
    with pytest.raises(HTTPException) as exc:
        await ensure_asset_in_scope(client, "asset-3", make_token(["album-a"]))
    assert exc.value.status_code == 404
    await client.aclose()


async def test_ensure_asset_in_scope_upstream_error_denies():
    client = make_client(lambda req: httpx.Response(500))
    with pytest.raises(HTTPException) as exc:
        await ensure_asset_in_scope(client, "asset-4", make_token(["album-a"]))
    assert exc.value.status_code == 404
    await client.aclose()


async def test_ensure_asset_in_scope_caches_album_lookup():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[{"id": "album-a"}])

    client = make_client(handler)
    await ensure_asset_in_scope(client, "asset-5", make_token(["album-a"]))
    await ensure_asset_in_scope(client, "asset-5", make_token(["album-a"]))
    assert len(calls) == 1  # second call served from cache
    await client.aclose()
