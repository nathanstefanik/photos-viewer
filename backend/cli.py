#!/usr/bin/env python3
"""Issue / revoke / list friend access tokens."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from app.config import settings
from app.tokens import TokenStore, format_token, normalize_token
from app.validation import UUID_PATTERN as _UUID


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage photos viewer access tokens")
    sub = parser.add_subparsers(dest="cmd", required=True)

    issue = sub.add_parser("issue", help="Create a new access code / link")
    issue.add_argument("--label", required=True, help="Friend label, e.g. friends")
    issue.add_argument(
        "--album",
        action="append",
        dest="albums",
        metavar="UUID",
        help="Restrict to Immich album UUID (repeatable). Omit for full library.",
    )
    issue.add_argument(
        "--expires-days",
        type=float,
        default=None,
        metavar="N",
        help="Code expires after N days (default: never)",
    )
    issue.add_argument(
        "--person",
        action="append",
        dest="persons",
        metavar="UUID",
        help="Immich person UUID (repeatable). Default view is 'photos of you'; "
        "doesn't restrict access, just what the guest sees first.",
    )

    revoke = sub.add_parser("revoke", help="Revoke a token by id")
    revoke.add_argument("token_id")

    scope = sub.add_parser("scope", help="Set album scope on an existing token")
    scope.add_argument("token_id", nargs="?", help="Token id from list")
    scope.add_argument(
        "--code",
        help="6-char access code (alternative to token_id)",
    )
    scope.add_argument(
        "--album",
        action="append",
        dest="albums",
        metavar="UUID",
        help="Immich album UUID (repeatable). Required unless --full.",
    )
    scope.add_argument(
        "--full",
        action="store_true",
        help="Remove album scope (full library access)",
    )

    sub.add_parser("list", help="List tokens")

    args = parser.parse_args()
    db_path = os.environ.get("TOKENS_DB_PATH", settings.tokens_db_path)
    base = os.environ.get("PUBLIC_BASE_URL", settings.public_base_url).rstrip("/")
    store = TokenStore(db_path, settings.session_secret)

    if args.cmd == "issue":
        album_ids = None
        if args.albums:
            album_ids = []
            for aid in args.albums:
                if not _UUID.match(aid):
                    print(f"invalid album UUID: {aid}", file=sys.stderr)
                    return 1
                album_ids.append(aid.lower())

        person_ids = None
        if args.persons:
            person_ids = []
            for pid in args.persons:
                if not _UUID.match(pid):
                    print(f"invalid person UUID: {pid}", file=sys.stderr)
                    return 1
                person_ids.append(pid.lower())

        expires_at = None
        if args.expires_days is not None:
            if args.expires_days <= 0:
                print("--expires-days must be positive", file=sys.stderr)
                return 1
            expires_at = time.time() + args.expires_days * 86400

        record, raw = store.issue(
            args.label, album_ids=album_ids, expires_at=expires_at, person_ids=person_ids
        )
        code = format_token(raw)
        link = f"{base}/t/{normalize_token(raw)}"
        print(f"id:      {record.id}")
        print(f"label:   {record.label}")
        print(f"code:    {code}")
        print(f"link:    {link}")
        if record.album_ids:
            print(f"albums:  {', '.join(record.album_ids)}")
        else:
            print("albums:  (full library)")
        if record.person_ids:
            print(f"people:  {', '.join(record.person_ids)} (default view, not restricted to)")
        print(f"expires: {_fmt_ts(record.expires_at)}")
        print("(say the code out loud, or send the link)")
        return 0

    if args.cmd == "revoke":
        ok = store.revoke(args.token_id)
        if not ok:
            print(f"not found or already revoked: {args.token_id}", file=sys.stderr)
            return 1
        print(f"revoked: {args.token_id}")
        return 0

    if args.cmd == "scope":
        token_id = args.token_id
        if args.code:
            record = store.lookup_raw(args.code)
            if not record:
                print(f"unknown or revoked code: {args.code}", file=sys.stderr)
                return 1
            token_id = record.id
        if not token_id:
            print("token_id or --code required", file=sys.stderr)
            return 1

        if args.full:
            album_ids = None
        else:
            if not args.albums:
                print("--album or --full required", file=sys.stderr)
                return 1
            album_ids = []
            for aid in args.albums:
                if not _UUID.match(aid):
                    print(f"invalid album UUID: {aid}", file=sys.stderr)
                    return 1
                album_ids.append(aid.lower())

        ok = store.set_album_scope(token_id, album_ids)
        if not ok:
            print(f"not found or revoked: {token_id}", file=sys.stderr)
            return 1
        record = store.get(token_id)
        if record.album_ids:
            print(f"scoped: {token_id} -> {', '.join(record.album_ids)}")
        else:
            print(f"scoped: {token_id} -> (full library)")
        return 0

    if args.cmd == "list":
        rows = store.list()
        if not rows:
            print("(no tokens)")
            return 0
        print(
            f"{'id':<12} {'label':<14} {'created':<18} {'expires':<18} "
            f"{'last_used':<18} {'scope':<8} status"
        )
        for r in rows:
            if r.revoked:
                status = "revoked"
            elif r.expired:
                status = "expired"
            else:
                status = "active"
            scope = f"{len(r.album_ids)} alb" if r.album_ids is not None else "full"
            print(
                f"{r.id:<12} {r.label[:14]:<14} {_fmt_ts(r.created_at):<18} "
                f"{_fmt_ts(r.expires_at):<18} {_fmt_ts(r.last_used_at):<18} "
                f"{scope:<8} {status}"
            )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
