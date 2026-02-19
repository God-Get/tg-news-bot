"""Add trend discovery topic profiles and moderation candidates

Revision ID: 0006_trend_discovery_candidates
Revises: 0005_trends_quality_semantic
Create Date: 2026-02-19 12:30:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_trend_discovery_candidates"
down_revision = "0005_trends_quality_semantic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column("trend_candidates_topic_id", sa.BigInteger(), nullable=True),
    )

    op.create_table(
        "trend_topic_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("seed_keywords", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("exclude_keywords", postgresql.JSONB(), nullable=True),
        sa.Column("trusted_domains", postgresql.JSONB(), nullable=True),
        sa.Column("min_article_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    trend_candidate_status = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "REJECTED",
        "INGESTED",
        "FAILED",
        name="trend_candidate_status",
        create_type=False,
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type WHERE typname = 'trend_candidate_status'
            ) THEN
                CREATE TYPE trend_candidate_status AS ENUM (
                    'PENDING',
                    'APPROVED',
                    'REJECTED',
                    'INGESTED',
                    'FAILED'
                );
            END IF;
        END $$;
        """
    )

    op.create_table(
        "trend_topics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("trend_topic_profiles.id"), nullable=True),
        sa.Column("topic_name", sa.Text(), nullable=False),
        sa.Column("topic_slug", sa.Text(), nullable=False),
        sa.Column("trend_score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("status", trend_candidate_status, nullable=False, server_default="PENDING"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_trend_topics_slug_discovered_at",
        "trend_topics",
        ["topic_slug", "discovered_at"],
        unique=False,
    )

    op.create_table(
        "trend_article_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("trend_topics.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False, unique=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("status", trend_candidate_status, nullable=False, server_default="PENDING"),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=True),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id_telegram", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_trend_article_candidates_topic_status_score",
        "trend_article_candidates",
        ["topic_id", "status", "score"],
        unique=False,
    )

    op.create_table(
        "trend_source_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("topic_id", sa.Integer(), sa.ForeignKey("trend_topics.id"), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("status", trend_candidate_status, nullable=False, server_default="PENDING"),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id_telegram", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "uq_trend_source_candidates_topic_domain",
        "trend_source_candidates",
        ["topic_id", "domain"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_trend_source_candidates_topic_domain",
        table_name="trend_source_candidates",
    )
    op.drop_table("trend_source_candidates")

    op.drop_index(
        "ix_trend_article_candidates_topic_status_score",
        table_name="trend_article_candidates",
    )
    op.drop_table("trend_article_candidates")

    op.drop_index("ix_trend_topics_slug_discovered_at", table_name="trend_topics")
    op.drop_table("trend_topics")

    op.drop_table("trend_topic_profiles")
    op.drop_column("bot_settings", "trend_candidates_topic_id")

    op.execute("DROP TYPE IF EXISTS trend_candidate_status")
