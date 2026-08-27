"""Per-asset emoji reactions and comments (guest-scoped, stored locally; Immich stays read-only)."""

from __future__ import annotations

import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import require_token
from ..deps import ensure_guest_id, get_client, get_social, resolve_person_name, validate_uuid
from ..schemas import CommentPayload, IdentityPayload, ReactionPayload
from ..scope import ensure_asset_in_scope, person_ids_in_scope
from ..social import SocialStore, default_display_name, normalize_display_name
from ..tokens import TokenRecord

router = APIRouter(prefix="/api")


async def _identity_for_request(
    payload: IdentityPayload,
    client: httpx.AsyncClient,
    token: TokenRecord,
) -> tuple[str, Optional[str]]:
    if payload.personId:
        allowed = await person_ids_in_scope(client, token)
        if allowed is not None and payload.personId not in allowed:
            raise HTTPException(status_code=400, detail="Person not found")
    person_name = await resolve_person_name(client, payload.personId)
    if payload.personId and not person_name:
        raise HTTPException(status_code=400, detail="Person not found")
    if person_name:
        return person_name, payload.personId
    return normalize_display_name(payload.displayName, default_display_name()), None


def _reaction_json(r) -> dict:
    return {
        "emoji": r.emoji,
        "count": r.count,
        "reacted": r.reacted,
        "names": r.names,
    }


def _comment_json(c) -> dict:
    return {
        "id": c.id,
        "displayName": c.display_name,
        "personId": c.person_id,
        "body": c.body,
        "createdAt": c.created_at,
        "mine": c.mine,
    }


@router.get("/assets/{asset_id}/social")
async def get_asset_social(
    asset_id: str,
    request: Request,
    response: Response,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    store: SocialStore = Depends(get_social),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    guest_id = ensure_guest_id(request, response)
    reactions = store.list_reactions(asset_id, guest_id)
    comments = store.list_comments(asset_id, guest_id)
    return {
        "guestId": guest_id,
        "reactions": [_reaction_json(r) for r in reactions],
        "comments": [_comment_json(c) for c in comments],
    }


@router.post("/assets/{asset_id}/reactions")
async def toggle_asset_reaction(
    asset_id: str,
    payload: ReactionPayload,
    request: Request,
    response: Response,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    store: SocialStore = Depends(get_social),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    guest_id = ensure_guest_id(request, response)
    display_name, person_id = await _identity_for_request(payload, client, token)
    try:
        reactions = store.toggle_reaction(
            asset_id=asset_id,
            guest_id=guest_id,
            emoji=payload.emoji,
            display_name=display_name,
            person_id=person_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"reactions": [_reaction_json(r) for r in reactions]}


@router.post("/assets/{asset_id}/comments")
async def add_asset_comment(
    asset_id: str,
    payload: CommentPayload,
    request: Request,
    response: Response,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
    store: SocialStore = Depends(get_social),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    guest_id = ensure_guest_id(request, response)
    display_name, person_id = await _identity_for_request(payload, client, token)
    try:
        comment = store.add_comment(
            asset_id=asset_id,
            guest_id=guest_id,
            body=payload.body,
            display_name=display_name,
            person_id=person_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _comment_json(comment)


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    request: Request,
    response: Response,
    token: TokenRecord = Depends(require_token),
    store: SocialStore = Depends(get_social),
):
    if not re.fullmatch(r"[0-9a-f]{32}", comment_id, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid comment id")
    guest_id = ensure_guest_id(request, response)
    if not store.delete_comment(comment_id, guest_id):
        raise HTTPException(status_code=404, detail="Comment not found")
    return {"ok": True}
