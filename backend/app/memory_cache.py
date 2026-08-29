"""In-memory TTL cache for people lists, search suggestions, and album-scope lookups.

Distinct from media_cache, which persists Immich thumbnail/original bytes on disk.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, Optional


class CacheEntry:
    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class CacheManager:
    """Thread-safe in-memory cache manager."""

    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        with self._lock:
            self._cache[key] = CacheEntry(value, ttl)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def cleanup_expired(self) -> int:
        with self._lock:
            expired_keys = [key for key, entry in self._cache.items() if entry.is_expired()]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def stats(self) -> dict:
        with self._lock:
            valid = sum(1 for e in self._cache.values() if not e.is_expired())
            expired = len(self._cache) - valid
            return {
                "total_entries": len(self._cache),
                "valid_entries": valid,
                "expired_entries": expired,
            }


cache_manager = CacheManager()
