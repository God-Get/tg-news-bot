"""Database models."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Text,
    Index,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from tg_news_bot.db.base import Base


class DraftState(enum.StrEnum):
    INBOX = "INBOX"
    EDITING = "EDITING"
    READY = "READY"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    ARCHIVE = "ARCHIVE"


class ImageStatus(enum.StrEnum):
    OK = "OK"
    NO_IMAGE = "NO_IMAGE"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
    NEEDS_RETRY = "NEEDS_RETRY"


class EditSessionStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ScheduledPostStatus(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ScheduleInputStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PublishFailureContext(enum.StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class TrendSignalSource(enum.StrEnum):
    ARXIV = "ARXIV"
    HN = "HN"
    X = "X"
    REDDIT = "REDDIT"


class TrendCandidateStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INGESTED = "INGESTED"
    FAILED = "FAILED"


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inbox_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    editing_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ready_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scheduled_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trend_candidates_topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    articles: Mapped[list[Article]] = relationship("Article", back_populates="source")
    drafts: Mapped[list[Draft]] = relationship("Draft", back_populates="source")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source: Mapped[Source | None] = relationship("Source", back_populates="articles")
    drafts: Mapped[list[Draft]] = relationship("Draft", back_populates="article")
    llm_cache: Mapped[LLMCache | None] = relationship(
        "LLMCache", back_populates="article", uselist=False
    )


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[DraftState] = mapped_column(
        Enum(DraftState, name="draft_state"),
        nullable=False,
        server_default=DraftState.INBOX.value,
    )

    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), nullable=True)

    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    post_text_ru: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    image_status: Mapped[ImageStatus] = mapped_column(
        Enum(ImageStatus, name="image_status"),
        nullable=False,
        server_default=ImageStatus.NO_IMAGE.value,
    )
    tg_image_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_image_unique_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    post_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    card_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    source: Mapped[Source | None] = relationship("Source", back_populates="drafts")
    article: Mapped[Article | None] = relationship("Article", back_populates="drafts")
    edit_session: Mapped[EditSession | None] = relationship(
        "EditSession", back_populates="draft", uselist=False
    )
    scheduled_post: Mapped[ScheduledPost | None] = relationship(
        "ScheduledPost", back_populates="draft", uselist=False
    )
    publish_failures: Mapped[list[PublishFailure]] = relationship(
        "PublishFailure",
        back_populates="draft",
    )


class EditSession(Base):
    __tablename__ = "edit_sessions"
    __table_args__ = (
        Index(
            "uq_edit_sessions_active_draft",
            "draft_id",
            unique=True,
            postgresql_where=sql_text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), nullable=False)

    group_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    instruction_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[EditSessionStatus] = mapped_column(
        Enum(EditSessionStatus, name="edit_session_status"),
        nullable=False,
        server_default=EditSessionStatus.ACTIVE.value,
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    draft: Mapped[Draft] = relationship("Draft", back_populates="edit_session")


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), nullable=False, unique=True)

    schedule_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ScheduledPostStatus] = mapped_column(
        Enum(ScheduledPostStatus, name="scheduled_post_status"),
        nullable=False,
        server_default=ScheduledPostStatus.SCHEDULED.value,
    )
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    draft: Mapped[Draft] = relationship("Draft", back_populates="scheduled_post")
    publish_failures: Mapped[list[PublishFailure]] = relationship(
        "PublishFailure",
        back_populates="scheduled_post",
    )


class LLMCache(Base):
    __tablename__ = "llm_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("articles.id"), nullable=True, unique=True
    )
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    topic_hints: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    title_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ru: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    article: Mapped[Article | None] = relationship("Article", back_populates="llm_cache")


class ScheduleInputSession(Base):
    __tablename__ = "schedule_input_sessions"
    __table_args__ = (
        Index(
            "uq_schedule_input_active_draft",
            "draft_id",
            unique=True,
            postgresql_where=sql_text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), nullable=False)
    group_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[ScheduleInputStatus] = mapped_column(
        Enum(ScheduleInputStatus, name="schedule_input_status"),
        nullable=False,
        server_default=ScheduleInputStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PublishFailure(Base):
    __tablename__ = "publish_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), nullable=False)
    scheduled_post_id: Mapped[int | None] = mapped_column(
        ForeignKey("scheduled_posts.id"), nullable=True
    )
    context: Mapped[PublishFailureContext] = mapped_column(
        Enum(PublishFailureContext, name="publish_failure_context"),
        nullable=False,
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    draft: Mapped[Draft] = relationship("Draft", back_populates="publish_failures")
    scheduled_post: Mapped[ScheduledPost | None] = relationship(
        "ScheduledPost", back_populates="publish_failures"
    )


class TrendSignal(Base):
    __tablename__ = "trend_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[TrendSignalSource] = mapped_column(
        Enum(TrendSignalSource, name="trend_signal_source"),
        nullable=False,
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SemanticFingerprint(Base):
    __tablename__ = "semantic_fingerprints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    text_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TrendTopicProfile(Base):
    __tablename__ = "trend_topic_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    seed_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    exclude_keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    trusted_domains: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    min_article_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TrendTopic(Base):
    __tablename__ = "trend_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("trend_topic_profiles.id"), nullable=True
    )
    topic_name: Mapped[str] = mapped_column(Text, nullable=False)
    topic_slug: Mapped[str] = mapped_column(Text, nullable=False)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[TrendCandidateStatus] = mapped_column(
        Enum(TrendCandidateStatus, name="trend_candidate_status"),
        nullable=False,
        server_default=TrendCandidateStatus.PENDING.value,
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    profile: Mapped[TrendTopicProfile | None] = relationship("TrendTopicProfile")


class TrendArticleCandidate(Base):
    __tablename__ = "trend_article_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("trend_topics.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[TrendCandidateStatus] = mapped_column(
        Enum(TrendCandidateStatus, name="trend_candidate_status"),
        nullable=False,
        server_default=TrendCandidateStatus.PENDING.value,
    )
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id"), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id_telegram: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    topic: Mapped[TrendTopic] = relationship("TrendTopic")


class TrendSourceCandidate(Base):
    __tablename__ = "trend_source_candidates"
    __table_args__ = (
        Index(
            "uq_trend_source_candidates_topic_domain",
            "topic_id",
            "domain",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("trend_topics.id"), nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[TrendCandidateStatus] = mapped_column(
        Enum(TrendCandidateStatus, name="trend_candidate_status"),
        nullable=False,
        server_default=TrendCandidateStatus.PENDING.value,
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    topic_id_telegram: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    topic: Mapped[TrendTopic] = relationship("TrendTopic")
