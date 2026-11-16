import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://clinic:clinic@localhost:5432/clinic"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    retell_api_key: str = ""
    retell_webhook_secret: str = Field(default="change-me", min_length=8)
    public_base_url: str = "http://localhost:8000"
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
        if not value.startswith(("postgresql+asyncpg://", "sqlite+aiosqlite://")):
            raise ValueError("DATABASE_URL must use asyncpg or aiosqlite")
        return value

    @property
    def pms_url(self) -> str:
        return self.pms_base_url.rstrip("/") or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
