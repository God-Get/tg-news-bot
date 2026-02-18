"""Application configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RSSSettings(BaseModel):
    poll_interval_seconds: int = Field(900, ge=60)
    max_items_per_source: int = Field(50, ge=1, le=200)
    per_source_min_interval_seconds: int = Field(0, ge=0, le=86400)
    request_delay_seconds: float = Field(0.0, ge=0.0, le=5.0)
    dedup_title_window_hours: int = Field(72, ge=1, le=720)
    allow_insecure_ssl_fallback: bool = False
    insecure_ssl_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    blocked_url_keywords: list[str] = Field(default_factory=list)
    blocked_title_keywords: list[str] = Field(default_factory=list)


class ScoringSettings(BaseModel):
    min_length_chars: int = Field(800, ge=200)
    max_length_chars: int = Field(20000, ge=500)
    freshness_hours: int = Field(72, ge=1)
    min_score: float = Field(0.0)
    keyword_boosts: dict[str, float] = Field(default_factory=dict)
    domain_boosts: dict[str, float] = Field(default_factory=dict)
    title_keyword_multiplier: float = Field(1.3, ge=1.0, le=3.0)


class ImageFilterSettings(BaseModel):
    min_width: int = Field(600, ge=100)
    min_height: int = Field(400, ge=100)
    min_aspect_ratio: float = Field(0.6, gt=0)
    max_aspect_ratio: float = Field(2.0, gt=0)
    reject_extensions: list[str] = Field(default_factory=lambda: [".svg", ".ico"])
    reject_path_keywords: list[str] = Field(
        default_factory=lambda: ["logo", "icon", "sprite", "favicon"]
    )


class SchedulerSettings(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    poll_interval_seconds: int = Field(10, ge=1)
    batch_size: int = Field(20, ge=1, le=200)
    max_publish_attempts: int = Field(3, ge=1, le=20)
    retry_backoff_seconds: int = Field(60, ge=5, le=3600)
    recover_failed_after_seconds: int = Field(300, ge=10, le=86400)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value


class LLMSettings(BaseModel):
    enabled: bool = False
    provider: str = "openai_compat"
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout_seconds: float = Field(30.0, ge=5.0, le=120.0)
    temperature: float = Field(0.2, ge=0.0, le=1.0)
    max_retries: int = Field(2, ge=0, le=6)
    retry_backoff_seconds: float = Field(1.0, ge=0.1, le=30.0)
    circuit_breaker_threshold: int = Field(5, ge=1, le=50)
    circuit_breaker_cooldown_seconds: float = Field(120.0, ge=5.0, le=3600.0)


class TextGenerationSettings(BaseModel):
    summary_max_chars: int = Field(900, ge=200, le=3000)
    keep_lang_prefix: bool = False


class PostFormattingSettings(BaseModel):
    sections_order: str = "title,body,hashtags,source"
    hashtags_limit: int = Field(5, ge=0, le=10)
    fallback_hashtag: str = "news"
    source_label: str = "Источник"
    source_mode: str = "button"
    discussion_url: str | None = None
    discussion_label: str = "Обсуждение"
    section_separator: str = "\n\n"

    @field_validator("source_mode")
    @classmethod
    def validate_source_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"text", "button", "both"}:
            raise ValueError("source_mode must be one of: text, button, both")
        return mode

    @field_validator("discussion_url")
    @classmethod
    def validate_discussion_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return text
        if parsed.scheme == "tg":
            return text
        raise ValueError("discussion_url must be a valid http(s) or tg URL")

    @field_validator("section_separator")
    @classmethod
    def normalize_section_separator(cls, value: str) -> str:
        return (
            value.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )


class HealthSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",
        env_prefix="",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database_url: str = Field(..., validation_alias="DATABASE_URL")
    bot_token: str = Field(..., validation_alias="BOT_TOKEN")
    admin_user_id: int = Field(..., validation_alias="ADMIN_USER_ID")
    log_level: str = Field("INFO", validation_alias="LOG_LEVEL")
    sentry_dsn: str | None = Field(default=None, validation_alias="SENTRY_DSN")

    rss: RSSSettings = RSSSettings()
    scoring: ScoringSettings = ScoringSettings()
    images: ImageFilterSettings = ImageFilterSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    llm: LLMSettings = LLMSettings()
    text_generation: TextGenerationSettings = TextGenerationSettings()
    post_formatting: PostFormattingSettings = PostFormattingSettings()
    health: HealthSettings = HealthSettings()

    extracted_text_ttl_days: int = Field(14, ge=1)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["bot_token"] = "***"
        if isinstance(data.get("llm"), dict) and data["llm"].get("api_key"):
            data["llm"]["api_key"] = "***"
        return data
