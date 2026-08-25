"""In-memory per-IP rate limit for short-code guessing on /t/{code}."""

from __future__ import annotations

import time

_REDEEM_WINDOW = 60.0
_REDEEM_MAX = 20

_redeem_attempts: dict[str, list[float]] = {}


def redeem_allowed(ip: str) -> bool:
    now = time.time()
    bucket = [t for t in _redeem_attempts.get(ip, []) if now - t < _REDEEM_WINDOW]
    if len(bucket) >= _REDEEM_MAX:
        _redeem_attempts[ip] = bucket
        return False
    bucket.append(now)
    _redeem_attempts[ip] = bucket
    return True
