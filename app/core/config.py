import os
import re
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(value: str) -> str:
    """Accept the URL exactly as Neon, Railway or Heroku-style hosts print it.

    Those hosts give `postgres://` or `postgresql://` with libpq options such as `sslmode` and
    `channel_binding`. asyncpg needs the `postgresql+asyncpg://` driver prefix, takes `ssl` instead
    of `sslmode`, and rejects `channel_binding`, so pasting the host's string unchanged would crash.
    """
    value = value.strip()
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            value = "postgresql+asyncpg://" + value[len(prefix):]
            break
    if not value.startswith("postgresql+asyncpg://"):
        return value
    # Neon's pooled endpoint (PgBouncer, transaction mode) breaks asyncpg's prepared-statement cache.
    # The direct endpoint is the same hostname without "-pooler", and this app needs few connections.
    value = re.sub(r"@(ep-[^.@/]+)-pooler\.", r"@\1.", value, count=1)
    if "?" not in value:
        return value
    base, query = value.split("?", 1)
    params = []
    for item in query.split("&"):
        key, _, val = item.partition("=")
        if key == "channel_binding":
            continue
        if key == "sslmode":
            key = "ssl"
        params.append(f"{key}={val}" if _ else key)
    return f"{base}?{'&'.join(params)}" if params else base


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://clinic:clinic@localhost:5432/clinic"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    retell_api_key: str = ""
    retell_webhook_secret: str = Field(default="change-me", min_length=8)
    # Render sets RENDER_EXTERNAL_URL to the service's public https address, so it needs no manual value.
    public_base_url: str = Field(
        default_factory=lambda: os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000"))
    clinic_timezone: str = "Asia/Kolkata"
    booking_buffer_minutes: int = Field(default=15, ge=0, le=240)
    # Empty means "the mock EHR inside this same server", on whatever port the server listens on.
    # Railway injects $PORT, so a hard-coded localhost:8000 silently breaks every booking there.
    pms_base_url: str = ""
    pms_failure_rate: float = Field(default=0, ge=0, le=1)
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def async_driver_required(cls, value: str) -> str:
        value = normalize_database_url(value)
        if not value.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (or sqlite+aiosqlite for tests)")
        return value

    @property
    def pms_url(self) -> str:
        return self.pms_base_url.rstrip("/") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
