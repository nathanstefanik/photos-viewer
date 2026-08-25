"""
Gated photos viewer — FastAPI read-only proxy to Immich.
Read-only is the absence of mutation routes; Immich API key is server-side only.

App wiring only: lifespan, middleware, static mounts, router registration.
Route handlers live in .routers; shared deps/schemas live in .deps / .schemas.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import current_token_id, unauthenticated_response
from .cache import cache_manager
from .config import settings
from .deps import STATIC_DIR
from .routers import albums, assets, pages, people, search
from .routers import social as social_router
from .security_headers import install_security_headers
from .social import SocialStore
from .tokens import TokenStore

CACHE_CLEANUP_INTERVAL_SECONDS = 600


async def _cache_cleanup_loop() -> None:
    """The in-memory cache never evicts on its own; sweep expired entries periodically."""
    while True:
        await asyncio.sleep(CACHE_CLEANUP_INTERVAL_SECONDS)
        cache_manager.cleanup_expired()


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

    cleanup_task = asyncio.create_task(_cache_cleanup_loop())

    yield

    cleanup_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_task
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

install_security_headers(app)


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


app.include_router(pages.router)
app.include_router(people.router)
app.include_router(albums.router)
app.include_router(search.router)
app.include_router(assets.router)
app.include_router(social_router.router)

if (STATIC_DIR / "css").is_dir():
    app.mount("/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
if (STATIC_DIR / "js").is_dir():
    app.mount("/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")
if (STATIC_DIR / "fonts").is_dir():
    app.mount("/fonts", StaticFiles(directory=str(STATIC_DIR / "fonts")), name="fonts")
