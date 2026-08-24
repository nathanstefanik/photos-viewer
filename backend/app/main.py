"""
Gated photos viewer — FastAPI read-only proxy to Immich.
Read-only is the absence of mutation routes; Immich API key is server-side only.
"""

from fastapi import FastAPI, HTTPException, Query, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional, List
from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
import httpx
import re
import time
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from .config import settings
from .cache import cache_manager
from .tokens import TokenStore, TokenRecord, normalize_token
from .scope import ensure_asset_in_scope, album_ids_for_search
from .social import (
    SocialStore,
    default_display_name,
    is_single_emoji,
    new_guest_id,
    normalize_display_name,
)
from .auth import (
    SESSION_COOKIE,
    make_session_value,
    client_ip,
    current_token_id,
    require_session,
    require_token,
    session_cookie_max_age,
    unauthenticated_response,
)

GUEST_COOKIE = "viewer_guest"
GUEST_COOKIE_MAX_AGE = 60 * 60 * 24 * 365


UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def validate_uuid(value: str, field_name: str = "id") -> str:
    if not value or not UUID_PATTERN.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}: must be a valid UUID")
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.immich_api_key:
        print("ERROR: IMMICH_API_KEY is not configured!")

    app.state.token_store = TokenStore(settings.tokens_db_path, settings.session_secret)
    app.state.social_store = SocialStore(settings.tokens_db_path)

    timeout = httpx.Timeout(30.0, read=120.0)
    app.state.http_client = httpx.AsyncClient(
        base_url=settings.immich_url.rstrip("/"),
        headers={"x-api-key": settings.immich_api_key},
        timeout=timeout,
        follow_redirects=True,
    )

    try:
        response = await app.state.http_client.get("/api/server/ping")
        if response.status_code == 200:
            print(f"Connected to Immich at {settings.immich_url}")
        else:
            print(f"Immich returned status {response.status_code}")
    except Exception as e:
        print(f"Cannot connect to Immich: {e}")

    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="Photos Viewer",
    description="Gated read-only photo gallery",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path

    if path == "/api/health" or path.startswith("/t/"):
        return await call_next(request)

    if path == "/gate" or path.startswith("/css/") or path.startswith("/js/") or path.startswith("/fonts/"):
        return await call_next(request)
    if path in ("/immich-logo.svg", "/favicon.ico", "/favicon-32.png"):
        return await call_next(request)

    if path.startswith("/api/") or path == "/" or path.startswith("/index"):
        if not current_token_id(request):
            return unauthenticated_response(request)

    return await call_next(request)


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_social(request: Request) -> SocialStore:
    return request.app.state.social_store


def _guest_id_from_request(request: Request) -> Optional[str]:
    raw = (request.cookies.get(GUEST_COOKIE) or "").strip()
    if UUID_PATTERN.match(raw):
        return raw.lower()
    return None


def ensure_guest_id(request: Request, response: Response) -> str:
    guest_id = _guest_id_from_request(request)
    if guest_id:
        return guest_id
    guest_id = new_guest_id()
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        key=GUEST_COOKIE,
        value=guest_id,
        max_age=GUEST_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return guest_id


async def resolve_person_name(
    client: httpx.AsyncClient,
    person_id: Optional[str],
) -> Optional[str]:
    if not person_id:
        return None
    validate_uuid(person_id, "person_id")
    try:
        response = await client.get(f"/api/people/{person_id}")
        response.raise_for_status()
        name = (response.json() or {}).get("name")
        if name and str(name).strip():
            return str(name).strip()
    except httpx.HTTPError:
        return None
    return None


class SearchFilters(BaseModel):
    query: Optional[str] = Field(None, max_length=500)
    personIds: Optional[List[str]] = None
    make: Optional[str] = Field(None, max_length=100)
    model: Optional[str] = Field(None, max_length=100)
    country: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    takenAfter: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}")
    takenBefore: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}")
    type: Optional[str] = Field(None, pattern=r"^(IMAGE|VIDEO|ALL)$")
    page: int = Field(1, ge=1, le=1000)
    size: int = Field(50, ge=1, le=100)

    @field_validator("personIds")
    @classmethod
    def validate_person_ids(cls, v):
        if v:
            for pid in v:
                if not UUID_PATTERN.match(pid):
                    raise ValueError(f"Invalid person ID: {pid}")
        return v


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    size: int
    hasMore: bool


class IdentityPayload(BaseModel):
    displayName: Optional[str] = Field(None, max_length=64)
    personId: Optional[str] = None

    @field_validator("personId")
    @classmethod
    def validate_person_id(cls, v):
        if v is not None and not UUID_PATTERN.match(v):
            raise ValueError("Invalid person ID")
        return v


class ReactionPayload(IdentityPayload):
    emoji: str = Field(..., min_length=1, max_length=32)

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, v):
        if not is_single_emoji(v):
            raise ValueError("Must be a single emoji from the system emoji keyboard")
        return v


class CommentPayload(IdentityPayload):
    body: str = Field(..., min_length=1, max_length=1000)


@app.get("/api/health")
async def health_check(client: httpx.AsyncClient = Depends(get_client)):
    try:
        response = await client.get("/api/server/ping")
        immich_status = "connected" if response.status_code == 200 else "error"
    except Exception as e:
        immich_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "immich": immich_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


# Simple per-IP rate limit for short-code guessing (in-memory)
_redeem_attempts: dict[str, list[float]] = {}
_REDEEM_WINDOW = 60.0
_REDEEM_MAX = 20


def _redeem_allowed(ip: str) -> bool:
    now = time.time()
    bucket = [t for t in _redeem_attempts.get(ip, []) if now - t < _REDEEM_WINDOW]
    if len(bucket) >= _REDEEM_MAX:
        _redeem_attempts[ip] = bucket
        return False
    bucket.append(now)
    _redeem_attempts[ip] = bucket
    return True


@app.get("/t/{raw_token}")
async def redeem_token(raw_token: str, request: Request):
    ip = client_ip(request)
    if not _redeem_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many attempts; try again shortly")

    store: TokenStore = request.app.state.token_store
    code = normalize_token(raw_token)
    if len(code) != 6:
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    record = store.lookup_raw(code)
    if not record or not record.active:
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    cookie_age = session_cookie_max_age(record)
    if cookie_age <= 0:
        return RedirectResponse(url="/gate?error=invalid", status_code=302)

    store.touch(record.id)
    response = RedirectResponse(url="/", status_code=302)
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=make_session_value(record.id),
        max_age=cookie_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/gate")
async def gate_page():
    gate = STATIC_DIR / "gate.html"
    if not gate.exists():
        raise HTTPException(status_code=404, detail="gate page missing")
    return FileResponse(gate)


@app.get("/api/server-info")
async def get_server_info(
    client: httpx.AsyncClient = Depends(get_client),
    _auth: str = Depends(require_session),
):
    try:
        response = await client.get("/api/server/about")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get server info")


@app.get("/api/people")
async def get_people(
    client: httpx.AsyncClient = Depends(get_client),
    withHidden: bool = False,
    _auth: str = Depends(require_session),
):
    cache_key = f"people_{withHidden}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached

    try:
        response = await client.get("/api/people", params={"withHidden": withHidden})
        response.raise_for_status()
        data = response.json()
        people = data.get("people", data) if isinstance(data, dict) else data
        named_people = [p for p in people if p.get("name")]
        named_people.sort(key=lambda x: x.get("name", "").lower())
        result = {"people": named_people, "total": len(named_people)}
        cache_manager.set(cache_key, result, ttl=300)
        return result
    except httpx.HTTPStatusError as e:
        return {"people": [], "total": 0, "error": f"Failed to get people: {e.response.status_code}"}
    except Exception as e:
        return {"people": [], "total": 0, "error": f"Failed to get people: {str(e)}"}


@app.get("/api/people/{person_id}")
async def get_person(
    person_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    _auth: str = Depends(require_session),
):
    validate_uuid(person_id, "person_id")
    try:
        response = await client.get(f"/api/people/{person_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Person not found")


@app.get("/api/people/{person_id}/thumbnail")
async def get_person_thumbnail(
    person_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    _auth: str = Depends(require_session),
):
    validate_uuid(person_id, "person_id")
    try:
        response = await client.get(f"/api/people/{person_id}/thumbnail")
        response.raise_for_status()
        return StreamingResponse(
            iter([response.content]),
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "private, max-age=3600"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Thumbnail not found")


@app.post("/api/search")
async def search_assets(
    filters: SearchFilters,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    """Proxy search to Immich. Text query → CLIP smart search; otherwise metadata."""
    search_payload: dict = {"page": filters.page, "size": filters.size}
    if filters.personIds:
        search_payload["personIds"] = filters.personIds
    if filters.make:
        search_payload["make"] = filters.make
    if filters.model:
        search_payload["model"] = filters.model
    if filters.country:
        search_payload["country"] = filters.country
    if filters.city:
        search_payload["city"] = filters.city
    if filters.state:
        search_payload["state"] = filters.state
    if filters.takenAfter:
        search_payload["takenAfter"] = filters.takenAfter
    if filters.takenBefore:
        search_payload["takenBefore"] = filters.takenBefore
    if filters.type and filters.type != "ALL":
        search_payload["type"] = filters.type

    scoped_albums = album_ids_for_search(token)
    if scoped_albums is not None:
        if not scoped_albums:
            return PaginatedResponse(
                items=[], total=0, page=filters.page, size=filters.size, hasMore=False
            )
        search_payload["albumIds"] = scoped_albums

    use_smart = bool(filters.query and filters.query.strip())
    if use_smart:
        search_payload["query"] = filters.query.strip()
        endpoint = "/api/search/smart"
    else:
        endpoint = "/api/search/metadata"

    try:
        response = await client.post(endpoint, json=search_payload)
        response.raise_for_status()
        data = response.json()
        assets = data.get("assets", {})
        items = assets.get("items", [])
        total = assets.get("total", assets.get("count", len(items)))

        return PaginatedResponse(
            items=items,
            total=total if isinstance(total, int) else len(items),
            page=filters.page,
            size=filters.size,
            hasMore=len(items) >= filters.size,
        )
    except httpx.HTTPStatusError as e:
        error_detail = f"HTTP {e.response.status_code}"
        try:
            error_data = e.response.json()
            error_detail = error_data.get("message", error_data.get("detail", str(error_data)))
        except Exception:
            try:
                error_detail = e.response.text
            except Exception:
                pass
        raise HTTPException(status_code=e.response.status_code, detail=error_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


@app.get("/api/search/suggestions")
async def get_search_suggestions(
    make: Optional[str] = Query(None, max_length=100),
    model: Optional[str] = Query(None, max_length=100),
    country: Optional[str] = Query(None, max_length=100),
    city: Optional[str] = Query(None, max_length=100),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    """Aggregate Immich typeahead suggestions (v3 API requires ?type= per field).

    Pass make=/country= to narrow child lists; model=/city= to resolve the parent.
    """
    cache_key = f"search_suggestions:v2:{make or ''}:{model or ''}:{country or ''}:{city or ''}"
    if token.is_scoped:
        cache_key += f":{','.join(token.album_ids or [])}"
    cached = cache_manager.get(cache_key)
    if cached:
        return cached

    type_map = {
        "cameraMake": "camera-make",
        "cameraModel": "camera-model",
        "country": "country",
        "city": "city",
        "state": "state",
    }
    result = {key: [] for key in type_map}

    # Immich getCountries() ignores city; infer parent country from one matching asset
    if city and not country:
        try:
            payload: dict = {"page": 1, "size": 1, "city": city, "withExif": True}
            scoped = album_ids_for_search(token)
            if scoped is not None:
                if not scoped:
                    return result
                payload["albumIds"] = scoped
            response = await client.post("/api/search/metadata", json=payload)
            if response.status_code == 200:
                items = response.json().get("assets", {}).get("items", [])
                if items:
                    exif = items[0].get("exifInfo") or {}
                    inferred = exif.get("country") or items[0].get("country")
                    if inferred:
                        result["country"] = [inferred]
            cache_manager.set(cache_key, result, ttl=600)
            return result
        except httpx.HTTPError:
            return result

    try:
        for out_key, immich_type in type_map.items():
            params: dict = {"type": immich_type}
            if make and immich_type == "camera-model":
                params["make"] = make
            if model and immich_type == "camera-make":
                params["model"] = model
            if country and immich_type == "city":
                params["country"] = country

            if make and immich_type != "camera-model":
                continue
            if model and not make and immich_type != "camera-make":
                continue
            if country and immich_type != "city":
                continue

            response = await client.get("/api/search/suggestions", params=params)
            if response.status_code != 200:
                continue
            data = response.json()
            if isinstance(data, list):
                result[out_key] = [x for x in data if x]
            elif isinstance(data, dict):
                result[out_key] = data.get(out_key) or data.get(immich_type) or []
        cache_manager.set(cache_key, result, ttl=600)
        return result
    except httpx.HTTPError:
        return result


@app.get("/api/assets")
async def get_assets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    payload: dict = {"page": page, "size": size}
    scoped_albums = album_ids_for_search(token)
    if scoped_albums is not None:
        if not scoped_albums:
            return PaginatedResponse(items=[], total=0, page=page, size=size, hasMore=False)
        payload["albumIds"] = scoped_albums
    try:
        response = await client.post("/api/search/metadata", json=payload)
        response.raise_for_status()
        data = response.json()
        assets = data.get("assets", {})
        items = assets.get("items", [])
        total = assets.get("count", len(items))
        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            size=size,
            hasMore=len(items) >= size,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get assets")


@app.get("/api/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        response = await client.get(f"/api/assets/{asset_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@app.get("/api/assets/{asset_id}/thumbnail")
async def get_asset_thumbnail(
    asset_id: str,
    size: str = Query("thumbnail", pattern=r"^(thumbnail|preview)$"),
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        response = await client.get(f"/api/assets/{asset_id}/thumbnail", params={"size": size})
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "image/jpeg")

        if PIL_AVAILABLE and len(content) > 5 * 1024 * 1024:
            try:
                img = Image.open(BytesIO(content))
                if img.mode == "RGBA" and "jpeg" in content_type.lower():
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    rgb_img.paste(img, mask=img.split()[3])
                    img = rgb_img
                output = BytesIO()
                for quality in [85, 75, 65, 55]:
                    output.seek(0)
                    output.truncate(0)
                    img.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= 5 * 1024 * 1024:
                        break
                content = output.getvalue()
                content_type = "image/jpeg"
            except Exception as e:
                print(f"Warning: Failed to compress image {asset_id}: {e}")

        return StreamingResponse(
            iter([content]),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=86400"},
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Thumbnail not found")


@app.get("/api/assets/{asset_id}/original")
async def get_asset_original(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        req = client.build_request("GET", f"/api/assets/{asset_id}/original")
        response = await client.send(req, stream=True)
        response.raise_for_status()

        async def stream_content():
            async for chunk in response.aiter_bytes(chunk_size=65536):
                yield chunk
            await response.aclose()

        headers = {"Cache-Control": "private, max-age=3600"}
        if response.headers.get("content-length"):
            headers["Content-Length"] = response.headers["content-length"]

        return StreamingResponse(
            stream_content(),
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


@app.get("/api/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    try:
        meta_resp = await client.get(f"/api/assets/{asset_id}")
        meta_resp.raise_for_status()
        if meta_resp.json().get("type") == "VIDEO":
            raise HTTPException(status_code=403, detail="Video downloads are disabled")

        req = client.build_request("GET", f"/api/assets/{asset_id}/original")
        response = await client.send(req, stream=True)
        response.raise_for_status()

        filename = None
        cd = response.headers.get("content-disposition")
        if cd and "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"')
        if not filename:
            filename = f"{asset_id}.bin"

        async def stream_content():
            async for chunk in response.aiter_bytes(chunk_size=65536):
                yield chunk
            await response.aclose()

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, max-age=3600",
        }
        if "content-length" in response.headers:
            headers["Content-Length"] = response.headers["content-length"]

        return StreamingResponse(
            stream_content(),
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Asset not found")


async def _identity_for_request(
    payload: IdentityPayload,
    client: httpx.AsyncClient,
) -> tuple[str, Optional[str]]:
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


@app.get("/api/assets/{asset_id}/social")
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


@app.post("/api/assets/{asset_id}/reactions")
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
    display_name, person_id = await _identity_for_request(payload, client)
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


@app.post("/api/assets/{asset_id}/comments")
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
    display_name, person_id = await _identity_for_request(payload, client)
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


@app.delete("/api/comments/{comment_id}")
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


@app.get("/api/assets/{asset_id}/video/playback")
async def get_video_playback(
    asset_id: str,
    request: Request,
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    validate_uuid(asset_id, "asset_id")
    await ensure_asset_in_scope(client, asset_id, token)
    client_range = request.headers.get("range")
    range_header = client_range if client_range else "bytes=0-"
    try:
        req = client.build_request(
            "GET",
            f"/api/assets/{asset_id}/video/playback",
            headers={"Range": range_header},
        )
        response = await client.send(req, stream=True)
        response.raise_for_status()

        async def stream_content():
            async for chunk in response.aiter_bytes(chunk_size=65536):
                yield chunk
            await response.aclose()

        headers = {"Accept-Ranges": response.headers.get("accept-ranges", "bytes")}
        if "content-length" in response.headers:
            headers["Content-Length"] = response.headers["content-length"]
        if "content-range" in response.headers:
            headers["Content-Range"] = response.headers["content-range"]

        return StreamingResponse(
            stream_content(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "video/mp4"),
            headers=headers,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Video not found")


@app.get("/api/statistics")
async def get_statistics(
    client: httpx.AsyncClient = Depends(get_client),
    token: TokenRecord = Depends(require_token),
):
    # Library-wide stats would leak totals under album-scoped tokens
    if token.is_scoped:
        return {"photos": 0, "videos": 0, "usage": 0, "scoped": True}
    try:
        response = await client.get("/api/assets/statistics")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Failed to get statistics")


@app.get("/")
async def index(_auth: str = Depends(require_session)):
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="frontend missing")
    return FileResponse(index_path)


@app.get("/immich-logo.svg")
async def logo():
    path = STATIC_DIR / "immich-logo.svg"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/favicon.ico")
async def favicon_ico():
    path = STATIC_DIR / "favicon.ico"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/favicon-32.png")
async def favicon_png():
    path = STATIC_DIR / "favicon-32.png"
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


if (STATIC_DIR / "css").is_dir():
    app.mount("/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
if (STATIC_DIR / "js").is_dir():
    app.mount("/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")
if (STATIC_DIR / "fonts").is_dir():
    app.mount("/fonts", StaticFiles(directory=str(STATIC_DIR / "fonts")), name="fonts")
