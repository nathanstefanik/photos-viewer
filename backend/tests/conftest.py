"""Env vars must be set before any app module is imported: Settings() validates at import time."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret-not-for-prod-0123456789abcdef")
os.environ.setdefault("IMMICH_API_KEY", "test-key")
os.environ.setdefault("TOKENS_DB_PATH", "/tmp/photos-viewer-test-default.db")
