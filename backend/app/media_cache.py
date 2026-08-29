"""Size-capped on-disk cache for Immich previews, originals, and person faces.

Authorization is the caller's job: this store is keyed by asset/person id, not by
token. Scope checks must run before lookup or fill. Bytes live as files under
`root`; a sibling SQLite index tracks size and LRU order. Immich bytes are stored
as-is — never recompressed.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import AsyncIterator, Optional

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .proxy import stream_upstream

_CHUNK = 65536
_EVICT_WATERMARK = 0.9


@dataclass
class CachedMedia:
    path: Path
    content_type: str
    size_bytes: int
    mtime: float


def asset_thumb_key(asset_id: str, size: str) -> str:
    return f"a:{asset_id}:{size}"


def person_thumb_key(person_id: str) -> str:
    return f"p:{person_id}"


def asset_original_key(asset_id: str) -> str:
    return f"o:{asset_id}"


def as_response(
    cached: CachedMedia,
    request: Request,
    cache_control: str,
    extra_headers: Optional[dict] = None,
) -> Response:
    etag = f'"{cached.size_bytes:x}-{int(cached.mtime)}"'
    headers = {"Cache-Control": cache_control, "ETag": etag}
    if extra_headers:
        headers.update(extra_headers)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers=headers,
    )


class MediaCache:
    def __init__(self, root: str, max_bytes: int):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._inflight: dict[str, asyncio.Lock] = {}
        self._inflight_guard = asyncio.Lock()
        self._fills: dict[str, asyncio.Event] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.root / "index.db", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS entries (
                    key TEXT PRIMARY KEY,
                    relpath TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    last_access REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entries_lru ON entries(last_access)"
            )
            conn.commit()

    def _path_for(self, key: str) -> Path:
        kind, rest = key.split(":", 1)
        if kind == "a":
            asset_id, size = rest.rsplit(":", 1)
            return self.root / "a" / asset_id[:2] / f"{asset_id}.{size}"
        if kind == "p":
            return self.root / "p" / rest[:2] / rest
        if kind == "o":
            return self.root / "o" / rest[:2] / rest
        raise ValueError(f"unknown cache key: {key}")

    def lookup(self, key: str) -> Optional[CachedMedia]:
        path = self._path_for(key)
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT content_type, size_bytes FROM entries WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is None or not path.is_file():
                    if row is not None:
                        conn.execute("DELETE FROM entries WHERE key = ?", (key,))
                        conn.commit()
                    return None
                conn.execute(
                    "UPDATE entries SET last_access = ? WHERE key = ?",
                    (time.time(), key),
                )
                conn.commit()
            stat = path.stat()
            return CachedMedia(
                path=path,
                content_type=row["content_type"],
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )

    def store(self, key: str, body: bytes, content_type: str) -> CachedMedia:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            tmp.write_bytes(body)
            os.replace(tmp, path)
        except Exception:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        return self._commit_file(key, path, content_type)

    async def store_stream(self, key: str, chunks: AsyncIterator[bytes], content_type: str) -> CachedMedia:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                async for chunk in chunks:
                    fh.write(chunk)
            os.replace(tmp, path)
        except Exception:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        return self._commit_file(key, path, content_type)

    def _commit_file(self, key: str, path: Path, content_type: str) -> CachedMedia:
        stat = path.stat()
        relpath = str(path.relative_to(self.root))
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO entries (key, relpath, content_type, size_bytes, last_access)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        relpath = excluded.relpath,
                        content_type = excluded.content_type,
                        size_bytes = excluded.size_bytes,
                        last_access = excluded.last_access
                    """,
                    (key, relpath, content_type, stat.st_size, now),
                )
                conn.commit()
                self._evict(conn, keep=key)
            return CachedMedia(
                path=path,
                content_type=content_type,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )

    def _evict(self, conn: sqlite3.Connection, keep: str) -> None:
        total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()[0]
        if total <= self.max_bytes:
            return
        target = int(self.max_bytes * _EVICT_WATERMARK)
        rows = conn.execute(
            "SELECT key, relpath, size_bytes FROM entries WHERE key != ? ORDER BY last_access ASC",
            (keep,),
        ).fetchall()
        for row in rows:
            if total <= target:
                break
            with suppress(OSError):
                (self.root / row["relpath"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM entries WHERE key = ?", (row["key"],))
            total -= row["size_bytes"]
        conn.commit()

    @asynccontextmanager
    async def single_flight(self, key: str):
        async with self._inflight_guard:
            lock = self._inflight.setdefault(key, asyncio.Lock())
        async with lock:
            yield

    async def get_or_fill(self, key: str, filler) -> CachedMedia:
        hit = self.lookup(key)
        if hit:
            return hit
        async with self.single_flight(key):
            hit = self.lookup(key)
            if hit:
                return hit
            return await filler()

    async def fill_http(
        self,
        key: str,
        client: httpx.AsyncClient,
        url: str,
        params: Optional[dict] = None,
    ) -> CachedMedia:
        req = client.build_request("GET", url, params=params)
        response = await client.send(req, stream=True)
        try:
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
            return await self.store_stream(
                key, response.aiter_bytes(chunk_size=_CHUNK), content_type or "image/jpeg"
            )
        finally:
            await response.aclose()

    async def stream_or_cached(
        self,
        key: str,
        request: Request,
        client: httpx.AsyncClient,
        url: str,
        *,
        cache_control: str,
        extra_headers: Optional[dict] = None,
    ) -> Response:
        """Cache hit → FileResponse. Miss → stream Immich to the client and to disk.

        Videos skip the cache (range playback, large files) and pass through.
        Followers wait until the leader finishes writing so they don't stampede Immich.
        """
        extra = dict(extra_headers or {})
        hit = self.lookup(key)
        if hit:
            return as_response(hit, request, cache_control, extra)

        async with self._inflight_guard:
            done = self._fills.get(key)
            leader = done is None
            if leader:
                done = asyncio.Event()
                self._fills[key] = done

        if not leader:
            await done.wait()
            hit = self.lookup(key)
            if hit:
                return as_response(hit, request, cache_control, extra)
            return await self._stream_miss(key, client, url, cache_control, extra, None)

        return await self._stream_miss(key, client, url, cache_control, extra, done)

    async def _stream_miss(
        self,
        key: str,
        client: httpx.AsyncClient,
        url: str,
        cache_control: str,
        extra: dict,
        done: Optional[asyncio.Event],
    ) -> Response:
        req = client.build_request("GET", url)
        response = await client.send(req, stream=True)
        try:
            response.raise_for_status()
        except Exception:
            await response.aclose()
            self._finish_fill(key, done)
            raise

        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        headers = {"Cache-Control": cache_control, **extra}
        if content_type.startswith("video/"):
            self._finish_fill(key, done)
            return stream_upstream(response, default_media_type=content_type, headers=headers)
        if response.headers.get("content-length"):
            headers["Content-Length"] = response.headers["content-length"]
        return StreamingResponse(
            self._tee_and_cache(key, response, content_type or "application/octet-stream", done),
            media_type=content_type or "application/octet-stream",
            headers=headers,
        )

    def _finish_fill(self, key: str, done: Optional[asyncio.Event]) -> None:
        if done is None:
            return
        done.set()
        if self._fills.get(key) is done:
            self._fills.pop(key, None)

    async def _tee_and_cache(
        self,
        key: str,
        response: httpx.Response,
        content_type: str,
        done: Optional[asyncio.Event],
    ) -> AsyncIterator[bytes]:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        committed = False
        try:
            with open(tmp, "wb") as fh:
                async for chunk in response.aiter_bytes(chunk_size=_CHUNK):
                    fh.write(chunk)
                    yield chunk
            os.replace(tmp, path)
            self._commit_file(key, path, content_type)
            committed = True
        finally:
            await response.aclose()
            if not committed:
                with suppress(OSError):
                    tmp.unlink(missing_ok=True)
            self._finish_fill(key, done)
