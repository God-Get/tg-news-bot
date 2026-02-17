"""Initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-02-14 20:05:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("inbox_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("editing_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("ready_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("scheduled_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("published_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("archive_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("url", name="uq_sources_url"),
    )

    op.create_table(
        "articles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_text_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_url", name="uq_articles_normalized_url"),
    )

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("state", sa.Enum("INBOX", "EDITING", "READY", "SCHEDULED", "PUBLISHED", "ARCHIVE", name="draft_state"), nullable=False, server_default="INBOX"),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_text_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("score_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("post_text_ru", sa.Text(), nullable=True),
        sa.Column("source_image_url", sa.Text(), nullable=True),
        sa.Column("has_image", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("image_status", sa.Enum("OK", "NO_IMAGE", "REJECTED", "ERROR", "NEEDS_RETRY", name="image_status"), nullable=False, server_default="NO_IMAGE"),
        sa.Column("tg_image_file_id", sa.Text(), nullable=True),
        sa.Column("tg_image_unique_id", sa.Text(), nullable=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("post_message_id", sa.BigInteger(), nullable=True),
        sa.Column("card_message_id", sa.BigInteger(), nullable=True),
        sa.Column("published_message_id", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_url", name="uq_drafts_normalized_url"),
    )

    op.create_table(
        "edit_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("instruction_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "COMPLETED", "CANCELLED", "EXPIRED", name="edit_session_status"), nullable=False, server_default="ACTIVE"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("schedule_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Enum("SCHEDULED", "PUBLISHED", "CANCELLED", "FAILED", name="scheduled_post_status"), nullable=False, server_default="SCHEDULED"),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("draft_id", name="uq_scheduled_posts_draft_id"),
    )

    op.create_index(
        "uq_edit_sessions_active_draft",
        "edit_sessions",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_edit_sessions_active_draft", table_name="edit_sessions")
    op.drop_table("scheduled_posts")
    op.drop_table("edit_sessions")
    op.drop_table("drafts")
    op.drop_table("articles")
    op.drop_table("sources")
    op.drop_table("bot_settings")

    op.execute("DROP TYPE IF EXISTS scheduled_post_status")
    op.execute("DROP TYPE IF EXISTS edit_session_status")
    op.execute("DROP TYPE IF EXISTS image_status")
    op.execute("DROP TYPE IF EXISTS draft_state")
