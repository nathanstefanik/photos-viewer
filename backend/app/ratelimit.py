"""In-memory rate limits for short-code guessing on /t/{code}.

Two independent limits, both must allow an attempt:
- per-IP: the primary control, keyed by client_ip() (only trustworthy when
  fronted by a trusted proxy — see auth.is_trusted_proxy; otherwise every
  request looks like it comes from the same peer, which just makes the
  per-IP bucket behave like a second global bucket).
- global: a backstop that caps total guesses across all callers regardless
  of IP, so a spoofed or misattributed IP can't multiply the effective limit.
"""

from __future__ import annotations

import time

_REDEEM_WINDOW = 60.0
_REDEEM_MAX_PER_IP = 20
_REDEEM_MAX_GLOBAL = 120  # generous vs. legitimate concurrent friend logins

# Cap on distinct IPs tracked at once, so an attacker cycling through many
# source addresses can't grow this dict without bound.
_MAX_TRACKED_IPS = 10_000

_redeem_attempts: dict[str, list[float]] = {}
_global_attempts: list[float] = []


def _prune_stale_ips(now: float) -> None:
    stale = [
        ip
        for ip, attempts in _redeem_attempts.items()
        if not attempts or now - attempts[-1] >= _REDEEM_WINDOW
    ]
    for ip in stale:
        del _redeem_attempts[ip]


def redeem_allowed(ip: str) -> bool:
    """True if both the per-IP and global redeem budgets have room; records
    the attempt against both when it is."""
    now = time.time()

    global _global_attempts
    _global_attempts = [t for t in _global_attempts if now - t < _REDEEM_WINDOW]
    if len(_global_attempts) >= _REDEEM_MAX_GLOBAL:
        return False

    bucket = [t for t in _redeem_attempts.get(ip, []) if now - t < _REDEEM_WINDOW]
    if len(bucket) >= _REDEEM_MAX_PER_IP:
        _redeem_attempts[ip] = bucket
        return False

    bucket.append(now)
    _redeem_attempts[ip] = bucket
    _global_attempts.append(now)

    if len(_redeem_attempts) > _MAX_TRACKED_IPS:
        _prune_stale_ips(now)

    return True


def reset() -> None:
    """Test-only: clear all tracked attempts."""
    _redeem_attempts.clear()
    global _global_attempts
    _global_attempts = []
