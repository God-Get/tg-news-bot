from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str
    database_url: str  # postgresql+asyncpg://...

    openai_api_key: str | None = None

    fetch_limit_default: int = 20
    extracted_text_ttl_days: int = 14


settings = Settings()
