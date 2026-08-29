"""Size-capped on-disk cache for Immich thumbnails and person faces.

Authorization is the caller's job: this store is keyed by asset/person id, not by
token. Scope checks must run before lookup or fill. Bytes live as files under
`root`; a sibling SQLite index tracks size and LRU order.
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

from fastapi import Request
from fastapi.responses import FileResponse, Response

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


def as_response(cached: CachedMedia, request: Request, cache_control: str) -> Response:
    etag = f'"{cached.size_bytes:x}-{int(cached.mtime)}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={"Cache-Control": cache_control, "ETag": etag},
    )


class MediaCache:
    def __init__(self, root: str, max_bytes: int):
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._inflight: dict[str, asyncio.Lock] = {}
        self._inflight_guard = asyncio.Lock()
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
