"""Chronological feed of comments, reactions, and uploads. Deletions are never events."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_token
from ..deps import get_client, get_social
from ..scope import album_ids_for_search, ensure_asset_in_scope
from ..social import SocialStore
from ..tokens import TokenRecord
from ..validation import is_uuid

router = APIRouter(prefix="/api")

_UPLOAD_LOOKBACK_DAYS = 365


def _immich_timestamp(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


async def _asset_if_visible(
    client: httpx.AsyncClient,
    asset_id: str,
    token: TokenRecord,
) -> dict | None:
    if not is_uuid(asset_id):
        return None
    try:
        await ensure_asset_in_scope(client, asset_id, token)
        response = await client.get(f"/api/assets/{asset_id}")
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except (HTTPException, httpx.HTTPError):
        return None


async def _recent_uploads(
    client: httpx.AsyncClient,
    token: TokenRecord,
    limit: int,
) -> list[dict]:
    scoped = album_ids_for_search(token)
    if scoped is not None and not scoped:
        return []

    # Immich metadata search orders by capture date; createdAfter + local sort
    # approximates recent ingest. Deleted Immich assets never appear.
    created_after = (
        datetime.now(timezone.utc) - timedelta(days=_UPLOAD_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload: dict = {
        "page": 1,
        "size": min(max(limit, 1), 100),
        "createdAfter": created_after,
    }
    if scoped is not None:
        payload["albumIds"] = scoped

    try:
        response = await client.post("/api/search/metadata", json=payload)
        if response.status_code == 400:
            payload.pop("createdAfter", None)
            response = await client.post("/api/search/metadata", json=payload)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    items = (response.json() or {}).get("assets", {}).get("items") or []
    assets = [item for item in items if isinstance(item, dict) and item.get("id")]
    assets.sort(key=lambda a: a.get("createdAt") or "", reverse=True)
    return assets[:limit]


def _event(
    kind: str,
    event_id: str,
    created_at: float,
    asset_id: str,
    *,
    display_name: str | None = None,
    body: str | None = None,
    emoji: str | None = None,
    asset_type: str | None = None,
) -> dict:
    return {
        "type": kind,
        "id": event_id,
        "createdAt": created_at,
        "assetId": asset_id,
        "displayName": display_name,
        "body": body,
        "emoji": emoji,
        "assetType": asset_type,
    }


@router.get("/activity")
async def list_activity(
    limit: int = Query(50, ge=1, le=100),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    store: SocialStore = Depends(get_social),
):
    comments = store.list_recent_comments(limit)
    reactions = store.list_recent_reactions(limit)
    uploads = await _recent_uploads(client, token, limit)

    known = {asset["id"]: asset for asset in uploads}
    needed = list(
        {
            row.asset_id
            for row in (*comments, *reactions)
            if row.asset_id not in known
        }
    )
    resolved = await asyncio.gather(
        *[_asset_if_visible(client, asset_id, token) for asset_id in needed]
    )
    for asset_id, asset in zip(needed, resolved):
        if asset:
            known[asset_id] = asset

    events = []
    for comment in comments:
        asset = known.get(comment.asset_id)
        if not asset:
            continue
        events.append(
            _event(
                "comment",
                comment.id,
                comment.created_at,
                comment.asset_id,
                display_name=comment.display_name,
                body=comment.body,
                asset_type=asset.get("type"),
            )
        )
    for reaction in reactions:
        asset = known.get(reaction.asset_id)
        if not asset:
            continue
        events.append(
            _event(
                "reaction",
                reaction.id,
                reaction.created_at,
                reaction.asset_id,
                display_name=reaction.display_name,
                emoji=reaction.emoji,
                asset_type=asset.get("type"),
            )
        )
    for asset in uploads:
        events.append(
            _event(
                "upload",
                asset["id"],
                _immich_timestamp(asset.get("createdAt")),
                asset["id"],
                asset_type=asset.get("type"),
            )
        )

    events.sort(key=lambda e: e["createdAt"], reverse=True)
    return {"items": events[:limit]}
