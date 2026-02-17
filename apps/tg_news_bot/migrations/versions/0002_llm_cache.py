"""Add llm cache table

Revision ID: 0002_llm_cache
Revises: 0001_initial
Create Date: 2026-02-17 01:50:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_llm_cache"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=True),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("topic_hints", postgresql.JSONB(), nullable=True),
        sa.Column("title_ru", sa.Text(), nullable=True),
        sa.Column("summary_ru", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_url", name="uq_llm_cache_normalized_url"),
        sa.UniqueConstraint("article_id", name="uq_llm_cache_article_id"),
    )


def downgrade() -> None:
    op.drop_table("llm_cache")
