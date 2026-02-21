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


class TrendsSettings(BaseModel):
    enabled: bool = True
    collect_interval_seconds: int = Field(1800, ge=60, le=86400)
    lookback_hours: int = Field(48, ge=1, le=720)
    max_keywords: int = Field(200, ge=10, le=2000)
    min_keyword_length: int = Field(3, ge=2, le=32)
    max_keyword_length: int = Field(40, ge=6, le=128)
    max_boost_per_keyword: float = Field(1.5, ge=0.1, le=5.0)
    arxiv_feeds: list[str] = Field(
        default_factory=lambda: [
            "https://export.arxiv.org/rss/cs.AI",
            "https://export.arxiv.org/rss/astro-ph",
            "https://export.arxiv.org/rss/physics.app-ph",
        ]
    )
    reddit_feeds: list[str] = Field(
        default_factory=lambda: [
            "https://www.reddit.com/r/science/top/.json?t=day&limit=50",
            "https://www.reddit.com/r/technology/top/.json?t=day&limit=50",
            "https://www.reddit.com/r/MachineLearning/top/.json?t=day&limit=50",
        ]
    )
    x_feeds: list[str] = Field(default_factory=list)
    hn_top_n: int = Field(80, ge=10, le=500)


class TrendDiscoveryProfileSettings(BaseModel):
    name: str
    seed_keywords: list[str]
    exclude_keywords: list[str] = Field(default_factory=list)
    trusted_domains: list[str] = Field(default_factory=list)
    min_article_score: float = Field(1.2, ge=-10.0, le=20.0)
    enabled: bool = True


class TrendDiscoverySettings(BaseModel):
    enabled: bool = True
    mode: str = "suggest"
    default_window_hours: int = Field(24, ge=1, le=240)
    max_window_hours: int = Field(240, ge=1, le=720)
    default_topic_limit: int = Field(5, ge=1, le=20)
    max_topic_limit: int = Field(20, ge=1, le=50)
    item_limit_per_source: int = Field(60, ge=5, le=300)
    max_articles_per_topic: int = Field(10, ge=1, le=30)
    max_sources_per_topic: int = Field(6, ge=1, le=30)
    min_topic_score: float = Field(2.0, ge=0.0, le=100.0)
    article_snippet_chars: int = Field(280, ge=80, le=1200)
    github_trending_enabled: bool = True
    github_trending_url: str = "https://github.com/trending"
    steam_charts_enabled: bool = True
    steam_charts_url: str = "https://steamcharts.com/top"
    boxoffice_enabled: bool = True
    boxoffice_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.boxofficemojo.com/month/",
        ]
    )
    ai_enrichment: bool = True
    auto_ingest_min_score: float = Field(4.0, ge=0.0, le=100.0)
    auto_add_source_min_score: float = Field(4.0, ge=0.0, le=100.0)
    profiles: list[TrendDiscoveryProfileSettings] = Field(
        default_factory=lambda: [
            TrendDiscoveryProfileSettings(
                name="AI",
                seed_keywords=[
                    "ai",
                    "artificial intelligence",
                    "llm",
                    "gpt",
                    "openai",
                    "anthropic",
                    "deepmind",
                    "machine learning",
                    "inference",
                ],
                exclude_keywords=["casino", "betting", "giveaway"],
                min_article_score=1.3,
            ),
            TrendDiscoveryProfileSettings(
                name="Science",
                seed_keywords=[
                    "research",
                    "study",
                    "scientists",
                    "nature",
                    "cell",
                    "genome",
                    "quantum",
                    "biology",
                ],
                exclude_keywords=["sponsored", "promo"],
                min_article_score=1.1,
            ),
            TrendDiscoveryProfileSettings(
                name="Space",
                seed_keywords=[
                    "space",
                    "nasa",
                    "spacex",
                    "rocket",
                    "orbit",
                    "satellite",
                    "moon",
                    "mars",
                    "telescope",
                ],
                min_article_score=1.2,
            ),
            TrendDiscoveryProfileSettings(
                name="New Energy",
                seed_keywords=[
                    "battery",
                    "fusion",
                    "solar",
                    "wind",
                    "hydrogen",
                    "nuclear",
                    "grid",
                    "energy storage",
                    "decarbonization",
                ],
                min_article_score=1.2,
            ),
            TrendDiscoveryProfileSettings(
                name="Gaming",
                seed_keywords=[
                    "game",
                    "gaming",
                    "steam",
                    "playstation",
                    "xbox",
                    "nintendo",
                    "esports",
                    "trailer",
                    "unreal engine",
                ],
                exclude_keywords=["casino", "betting", "giveaway"],
                min_article_score=1.2,
            ),
            TrendDiscoveryProfileSettings(
                name="Movies",
                seed_keywords=[
                    "movie",
                    "film",
                    "box office",
                    "cinema",
                    "trailer",
                    "director",
                    "hollywood",
                    "streaming release",
                ],
                exclude_keywords=["gossip", "celebrity rumor"],
                min_article_score=1.1,
            ),
            TrendDiscoveryProfileSettings(
                name="Quantum Computing",
                seed_keywords=[
                    "quantum computing",
                    "qubit",
                    "quantum processor",
                    "quantum network",
                    "quantum error correction",
                    "ion trap",
                    "superconducting qubit",
                ],
                exclude_keywords=["quantum healing", "esoteric"],
                min_article_score=1.3,
            ),
            TrendDiscoveryProfileSettings(
                name="Programming",
                seed_keywords=[
                    "programming",
                    "developer",
                    "framework",
                    "release notes",
                    "open source",
                    "github",
                    "compiler",
                    "language update",
                    "api",
                ],
                exclude_keywords=["bootcamp ad", "course discount"],
                min_article_score=1.15,
            ),
            TrendDiscoveryProfileSettings(
                name="Gadgets",
                seed_keywords=[
                    "smartphone",
                    "laptop",
                    "wearable",
                    "chipset",
                    "camera sensor",
                    "headset",
                    "consumer electronics",
                    "benchmark",
                ],
                exclude_keywords=["accessories sale", "coupon"],
                min_article_score=1.1,
            ),
        ]
    )

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"off", "suggest", "auto"}:
            raise ValueError("trend_discovery.mode must be one of: off, suggest, auto")
        return mode


class InternetScoringSettings(BaseModel):
    enabled: bool = True
    lookback_hours: int = Field(48, ge=1, le=720)
    max_signal_keywords: int = Field(120, ge=10, le=2000)
    max_signal_matches_per_item: int = Field(8, ge=1, le=32)
    signal_keyword_multiplier: float = Field(0.35, ge=0.0, le=5.0)
    max_signal_boost_per_keyword: float = Field(0.8, ge=0.0, le=5.0)
    max_total_signal_boost: float = Field(2.5, ge=0.0, le=20.0)
    db_signal_multiplier: float = Field(0.12, ge=0.0, le=5.0)
    google_trends_enabled: bool = True
    google_trends_feeds: list[str] = Field(
        default_factory=lambda: [
            "https://trends.google.com/trending/rss?geo=RU",
        ]
    )
    google_trends_top_n: int = Field(40, ge=5, le=300)
    google_trends_token_weight: float = Field(0.2, ge=0.0, le=2.0)
    wordstat_keyword_boosts: dict[str, float] = Field(default_factory=dict)
    seed_hit_weight: float = Field(1.25, ge=0.0, le=10.0)
    exclude_hit_penalty: float = Field(1.5, ge=0.0, le=10.0)
    trusted_domain_bonus: float = Field(0.7, ge=0.0, le=5.0)
    source_trust_multiplier: float = Field(0.12, ge=0.0, le=2.0)
    source_trust_boost_cap: float = Field(1.0, ge=0.0, le=10.0)
    default_source_weight: float = Field(0.7, ge=-5.0, le=10.0)
    source_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "ARXIV": 1.25,
            "HN": 1.0,
            "REDDIT": 0.9,
            "X": 0.8,
            "GITHUB": 1.05,
            "STEAM_CHARTS": 1.0,
            "BOXOFFICE": 0.95,
            "GOOGLE_TRENDS": 1.1,
            "WORDSTAT": 1.0,
        }
    )


class SourceQualitySettings(BaseModel):
    enabled: bool = True
    auto_disable_enabled: bool = True
    auto_disable_threshold: float = Field(-4.0, ge=-20.0, le=0.0)
    min_events_for_auto_disable: int = Field(12, ge=1, le=200)
    consecutive_failures_disable_threshold: int = Field(8, ge=2, le=200)
    created_delta: float = Field(0.25, ge=0.0, le=5.0)
    duplicate_delta: float = Field(-0.2, ge=-5.0, le=0.0)
    blocked_delta: float = Field(-0.8, ge=-5.0, le=0.0)
    low_score_delta: float = Field(-0.35, ge=-5.0, le=0.0)
    no_html_delta: float = Field(-0.3, ge=-5.0, le=0.0)
    invalid_entry_delta: float = Field(-0.15, ge=-5.0, le=0.0)
    unsafe_delta: float = Field(-1.0, ge=-5.0, le=0.0)
    near_duplicate_delta: float = Field(-0.5, ge=-5.0, le=0.0)
    rss_http_error_delta: float = Field(-0.4, ge=-5.0, le=0.0)
    rss_http_403_delta: float = Field(-0.8, ge=-5.0, le=0.0)
    rss_empty_delta: float = Field(-0.25, ge=-5.0, le=0.0)
    high_duplicate_rate_delta: float = Field(-0.6, ge=-5.0, le=0.0)


class SemanticDedupSettings(BaseModel):
    enabled: bool = True
    dimensions: int = Field(128, ge=32, le=1024)
    similarity_threshold: float = Field(0.92, ge=0.5, le=0.999)
    lookback_hours: int = Field(120, ge=1, le=720)
    max_candidates: int = Field(600, ge=10, le=5000)
    store_vectors: bool = True


class ContentSafetySettings(BaseModel):
    enabled: bool = True
    min_ready_chars: int = Field(140, ge=20, le=5000)
    max_links_in_text: int = Field(6, ge=0, le=50)
    ad_keywords: list[str] = Field(
        default_factory=lambda: [
            "sponsored",
            "promo code",
            "subscribe now",
            "limited offer",
            "buy now",
            "casino",
            "betting",
        ]
    )
    toxic_keywords: list[str] = Field(
        default_factory=lambda: [
            "kill",
            "hate speech",
            "racist",
            "extremist propaganda",
        ]
    )


class QualityGateSettings(BaseModel):
    enabled: bool = True
    min_meaningful_chars: int = Field(120, ge=20, le=3000)
    min_words: int = Field(30, ge=5, le=500)
    fallback_snippet_chars: int = Field(800, ge=100, le=4000)
    max_paywall_marker_hits: int = Field(2, ge=1, le=20)


class AnalyticsSettings(BaseModel):
    default_window_hours: int = Field(24, ge=1, le=720)
    max_window_hours: int = Field(720, ge=24, le=8760)


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
    autoplan_peak_hours: list[int] = Field(default_factory=lambda: [9, 12, 18, 21])
    autoplan_peak_bonus: float = Field(0.6, ge=0.0, le=5.0)
    autoplan_topic_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "ai": 0.7,
            "space": 0.55,
            "science": 0.45,
            "energy": 0.35,
            "gaming": 0.6,
            "programming": 0.5,
            "gadgets": 0.45,
            "movies": 0.3,
            "quantum": 0.55,
        }
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @field_validator("autoplan_peak_hours")
    @classmethod
    def validate_autoplan_peak_hours(cls, value: list[int]) -> list[int]:
        cleaned: list[int] = []
        seen: set[int] = set()
        for raw_hour in value:
            hour = int(raw_hour)
            if hour < 0 or hour > 23:
                continue
            if hour in seen:
                continue
            seen.add(hour)
            cleaned.append(hour)
        return cleaned

    @field_validator("autoplan_topic_weights")
    @classmethod
    def validate_autoplan_topic_weights(cls, value: dict[str, float]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, raw_weight in value.items():
            topic = str(key).strip().lower()
            if not topic:
                continue
            result[topic] = max(min(float(raw_weight), 5.0), -5.0)
        return result


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
    defer_to_editing: bool = False
    translation_style: str = "journalistic"
    translation_refine_pass: bool = True
    translation_glossary: dict[str, str] = Field(default_factory=dict)

    @field_validator("translation_style")
    @classmethod
    def validate_translation_style(cls, value: str) -> str:
        style = value.strip().lower()
        if style not in {"journalistic", "neutral", "concise"}:
            raise ValueError(
                "translation_style must be one of: journalistic, neutral, concise"
            )
        return style


class PostFormattingSettings(BaseModel):
    sections_order: str = "title,body,hashtags,source"
    hashtags_limit: int = Field(5, ge=0, le=10)
    fallback_hashtag: str = "news"
    hashtag_mode: str = "both"
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

    @field_validator("hashtag_mode")
    @classmethod
    def validate_hashtag_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in {"ru", "en", "both"}:
            raise ValueError("hashtag_mode must be one of: ru, en, both")
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
    trends: TrendsSettings = TrendsSettings()
    trend_discovery: TrendDiscoverySettings = TrendDiscoverySettings()
    internet_scoring: InternetScoringSettings = InternetScoringSettings()
    source_quality: SourceQualitySettings = SourceQualitySettings()
    semantic_dedup: SemanticDedupSettings = SemanticDedupSettings()
    content_safety: ContentSafetySettings = ContentSafetySettings()
    quality_gate: QualityGateSettings = QualityGateSettings()
    analytics: AnalyticsSettings = AnalyticsSettings()
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
