"""Per-asset emoji reactions and comments. Stored locally; Immich stays read-only."""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .codes import generate_code

# One emoji grapheme: flags, ZWJ sequences, keycaps, skin tones, VS16
_EMOJI_RE = re.compile(
    r"^("
    r"[\U0001F1E0-\U0001F1FF]{2}"
    r"|[#*0-9]\uFE0F?\u20E3"
    r"|(?:[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    r"\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
    r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    r"\u2600-\u26FF\u2700-\u27BF\U0001F000-\U0001F02F]"
    r"[\U0001F3FB-\U0001F3FF]?"
    r"(?:\u200D[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FAFF\u2600-\u26FF\u2700-\u27BF"
    r"\U0001F3FB-\U0001F3FF\uFE0F]*)*"
    r"\uFE0F?)"
    r")$"
)

_DISPLAY_NAME_RE = re.compile(r"^[\w\s.'\-]{1,64}$", re.UNICODE)


def new_guest_id() -> str:
    return str(uuid.uuid4())


def default_display_name() -> str:
    return f"Guest-{generate_code(4)}"


def is_single_emoji(value: str) -> bool:
    if not value or len(value) > 32:
        return False
    return bool(_EMOJI_RE.match(value))


def normalize_display_name(name: Optional[str], fallback: str) -> str:
    if not name:
        return fallback
    cleaned = " ".join(name.strip().split())
    if not cleaned or not _DISPLAY_NAME_RE.match(cleaned):
        return fallback
    return cleaned[:64]


@dataclass
class ReactionGroup:
    emoji: str
    count: int
    reacted: bool
    names: list[str]


@dataclass
class ReactionRecord:
    id: str
    asset_id: str
    guest_id: str
    emoji: str
    display_name: str
    person_id: Optional[str]
    created_at: float


@dataclass
class CommentRecord:
    id: str
    asset_id: str
    guest_id: str
    display_name: str
    person_id: Optional[str]
    body: str
    created_at: float
    mine: bool = False


class SocialStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS reactions (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    guest_id TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    person_id TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(asset_id, guest_id, emoji)
                );
                CREATE INDEX IF NOT EXISTS idx_reactions_asset
                    ON reactions(asset_id);

                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    guest_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    person_id TEXT,
                    body TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_comments_asset
                    ON comments(asset_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_comments_created
                    ON comments(created_at);
                CREATE INDEX IF NOT EXISTS idx_reactions_created
                    ON reactions(created_at);
                """
            )
            conn.commit()

    def list_reactions(self, asset_id: str, guest_id: str) -> list[ReactionGroup]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT emoji, display_name, guest_id
                FROM reactions
                WHERE asset_id = ?
                ORDER BY created_at ASC
                """,
                (asset_id,),
            ).fetchall()

        grouped: dict[str, ReactionGroup] = {}
        for row in rows:
            emoji = row["emoji"]
            group = grouped.get(emoji)
            if not group:
                group = ReactionGroup(emoji=emoji, count=0, reacted=False, names=[])
                grouped[emoji] = group
            group.count += 1
            if row["display_name"] not in group.names and len(group.names) < 8:
                group.names.append(row["display_name"])
            if row["guest_id"] == guest_id:
                group.reacted = True

        return sorted(grouped.values(), key=lambda g: (-g.count, g.emoji))

    def toggle_reaction(
        self,
        asset_id: str,
        guest_id: str,
        emoji: str,
        display_name: str,
        person_id: Optional[str] = None,
    ) -> list[ReactionGroup]:
        if not is_single_emoji(emoji):
            raise ValueError("emoji must be a single emoji from the system emoji keyboard")

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM reactions
                WHERE asset_id = ? AND guest_id = ? AND emoji = ?
                """,
                (asset_id, guest_id, emoji),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM reactions WHERE id = ?", (existing["id"],))
            else:
                conn.execute(
                    """
                    INSERT INTO reactions (
                        id, asset_id, guest_id, emoji, display_name, person_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        asset_id,
                        guest_id,
                        emoji,
                        display_name,
                        person_id,
                        time.time(),
                    ),
                )
            conn.commit()

        return self.list_reactions(asset_id, guest_id)

    def list_comments(self, asset_id: str, guest_id: str) -> list[CommentRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, asset_id, guest_id, display_name, person_id, body, created_at
                FROM comments
                WHERE asset_id = ?
                ORDER BY created_at ASC
                """,
                (asset_id,),
            ).fetchall()
        return [
            CommentRecord(
                id=row["id"],
                asset_id=row["asset_id"],
                guest_id=row["guest_id"],
                display_name=row["display_name"],
                person_id=row["person_id"],
                body=row["body"],
                created_at=row["created_at"],
                mine=row["guest_id"] == guest_id,
            )
            for row in rows
        ]

    def add_comment(
        self,
        asset_id: str,
        guest_id: str,
        body: str,
        display_name: str,
        person_id: Optional[str] = None,
    ) -> CommentRecord:
        text = " ".join(body.strip().split())
        if not text:
            raise ValueError("comment cannot be empty")
        if len(text) > 1000:
            raise ValueError("comment too long")

        comment_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO comments (
                    id, asset_id, guest_id, display_name, person_id, body, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (comment_id, asset_id, guest_id, display_name, person_id, text, now),
            )
            conn.commit()
        return CommentRecord(
            id=comment_id,
            asset_id=asset_id,
            guest_id=guest_id,
            display_name=display_name,
            person_id=person_id,
            body=text,
            created_at=now,
            mine=True,
        )

    def delete_comment(self, comment_id: str, guest_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM comments WHERE id = ? AND guest_id = ?",
                (comment_id, guest_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_recent_comments(self, limit: int) -> list[CommentRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, asset_id, guest_id, display_name, person_id, body, created_at
                FROM comments
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            CommentRecord(
                id=row["id"],
                asset_id=row["asset_id"],
                guest_id=row["guest_id"],
                display_name=row["display_name"],
                person_id=row["person_id"],
                body=row["body"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_recent_reactions(self, limit: int) -> list[ReactionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, asset_id, guest_id, emoji, display_name, person_id, created_at
                FROM reactions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ReactionRecord(
                id=row["id"],
                asset_id=row["asset_id"],
                guest_id=row["guest_id"],
                emoji=row["emoji"],
                display_name=row["display_name"],
                person_id=row["person_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
