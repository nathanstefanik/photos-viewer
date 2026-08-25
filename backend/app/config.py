"""Configuration settings for the gated photos viewer."""

from pydantic_settings import BaseSettings, NoDecode
from pydantic import field_validator
from typing import Annotated, List
import json


class Settings(BaseSettings):
    immich_url: str = "http://127.0.0.1:2283"
    immich_api_key: str = ""

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    cors_origins: Annotated[List[str], NoDecode] = [
        "http://127.0.0.1:8080"
    ]
    public_base_url: str = "http://127.0.0.1:8080"

    session_secret: str = ""
    tokens_db_path: str = "/data/tokens.db"

    # X-Forwarded-For/X-Real-IP are attacker-controlled unless the direct TCP peer is
    # a trusted reverse proxy — trusting them unconditionally lets anyone bypass
    # IP-based rate limiting by forging the header. Off by default (safe: falls back
    # to the direct peer address). Enable only alongside trusted_proxy_ips below.
    trust_x_forwarded_for: bool = False
    # IPs or CIDR ranges (e.g. Caddy's address, or your Docker bridge network) allowed
    # to set X-Forwarded-For/X-Real-IP. Required for trust_x_forwarded_for to take effect.
    trusted_proxy_ips: Annotated[List[str], NoDecode] = []

    cache_ttl_people: int = 300
    cache_ttl_suggestions: int = 600

    @field_validator("cors_origins", "trusted_proxy_ips", mode="before")
    @classmethod
    def parse_str_list(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


settings = Settings()

if not settings.session_secret:
    raise RuntimeError(
        "SESSION_SECRET is required (openssl rand -hex 32). Refusing to start with an ephemeral secret."
    )
