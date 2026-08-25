"""Unambiguous short-code alphabet shared by access tokens and guest display names."""

from __future__ import annotations

import secrets

# No 0/O, 1/I — unambiguous for verbal relay and for reading off a screen
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_code(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
