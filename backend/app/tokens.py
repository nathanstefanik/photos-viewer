"""Per-friend access tokens stored in SQLite. Secrets are HMAC-hashed; only hashes persist."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .codes import generate_code

_TOKEN_LEN = 6


def normalize_token(raw: str) -> str:
    """Strip separators and uppercase so ABC-DEF / abc def all work."""
    return "".join(c for c in raw.upper() if c.isalnum())


def format_token(raw: str) -> str:
    """ABC-DEF for saying out loud."""
    raw = normalize_token(raw)
    if len(raw) == _TOKEN_LEN:
        return f"{raw[:3]}-{raw[3:]}"
    return raw


def hash_token(raw: str, secret: str) -> str:
    """HMAC-SHA256 keyed by SESSION_SECRET so a DB dump alone is not enough to crack codes."""
    if not secret:
        raise RuntimeError("SESSION_SECRET is required to hash access codes")
    return hmac.new(
        secret.encode("utf-8"),
        normalize_token(raw).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _legacy_hash_token(raw: str) -> str:
    """Pre-HMAC plain SHA-256; kept only for one-time upgrade on lookup."""
    return hashlib.sha256(normalize_token(raw).encode("utf-8")).hexdigest()


@dataclass
class TokenRecord:
    id: str
    label: str
    created_at: float
    last_used_at: Optional[float]
    revoked_at: Optional[float]
    expires_at: Optional[float]
    album_ids: Optional[list[str]]  # None = full library; list = scoped

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    @property
    def active(self) -> bool:
        return not self.revoked and not self.expired

    @property
    def is_scoped(self) -> bool:
        return self.album_ids is not None


class TokenStore:
    def __init__(self, db_path: str, secret: str):
        self.db_path = Path(db_path)
        self.secret = secret
        if not self.secret:
            raise RuntimeError("SESSION_SECRET is required")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL,
                    last_used_at REAL,
                    revoked_at REAL,
                    expires_at REAL,
                    album_ids TEXT
                )
                """
            )
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(tokens)")}
            if "expires_at" not in cols:
                conn.execute("ALTER TABLE tokens ADD COLUMN expires_at REAL")
            if "album_ids" not in cols:
                conn.execute("ALTER TABLE tokens ADD COLUMN album_ids TEXT")
            conn.commit()

    def issue(
        self,
        label: str,
        album_ids: Optional[list[str]] = None,
        expires_at: Optional[float] = None,
    ) -> tuple[TokenRecord, str]:
        """Create a token. Returns (record, raw_secret). Raw is shown once."""
        token_id = generate_code(8)
        album_json = json.dumps(album_ids) if album_ids is not None else None
        for _ in range(32):
            raw = generate_code(_TOKEN_LEN)
            try:
                now = time.time()
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO tokens (
                            id, label, token_hash, created_at, last_used_at,
                            revoked_at, expires_at, album_ids
                        )
                        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
                        """,
                        (
                            token_id,
                            label.strip() or "friend",
                            hash_token(raw, self.secret),
                            now,
                            expires_at,
                            album_json,
                        ),
                    )
                    conn.commit()
                record = TokenRecord(
                    id=token_id,
                    label=label.strip() or "friend",
                    created_at=now,
                    last_used_at=None,
                    revoked_at=None,
                    expires_at=expires_at,
                    album_ids=list(album_ids) if album_ids is not None else None,
                )
                return record, raw
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("could not allocate a unique access code")

    def lookup_raw(self, raw: str) -> Optional[TokenRecord]:
        token_hash = hash_token(raw, self.secret)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row:
                return self._row_to_record(row)

            # Upgrade legacy plain-SHA256 hashes on successful redeem
            legacy = _legacy_hash_token(raw)
            row = conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ?",
                (legacy,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE tokens SET token_hash = ? WHERE id = ?",
                (token_hash, row["id"]),
            )
            conn.commit()
            return self._row_to_record(row)

    def get(self, token_id: str) -> Optional[TokenRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE id = ?",
                (token_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def touch(self, token_id: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tokens SET last_used_at = ?
                WHERE id = ? AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (now, token_id, now),
            )
            conn.commit()

    def revoke(self, token_id: str) -> bool:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, token_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_album_scope(self, token_id: str, album_ids: Optional[list[str]]) -> bool:
        """Bind an existing token to album UUIDs. Pass None for full-library access."""
        album_json = json.dumps(album_ids) if album_ids is not None else None
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE tokens SET album_ids = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (album_json, token_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list(self) -> list[TokenRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tokens ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TokenRecord:
        album_raw = row["album_ids"] if "album_ids" in row.keys() else None
        album_ids = None
        if album_raw:
            album_ids = json.loads(album_raw)
        return TokenRecord(
            id=row["id"],
            label=row["label"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            revoked_at=row["revoked_at"],
            expires_at=row["expires_at"] if "expires_at" in row.keys() else None,
            album_ids=album_ids,
        )
