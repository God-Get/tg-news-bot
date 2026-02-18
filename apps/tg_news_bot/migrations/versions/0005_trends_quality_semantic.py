"""Add trends, source trust score, and semantic dedup storage

Revision ID: 0005_trends_quality_semantic
Revises: 0004_publish_failures
Create Date: 2026-02-18 11:20:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_trends_quality_semantic"
down_revision = "0004_publish_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("trust_score", sa.Float(), nullable=False, server_default="0"),
    )

    op.create_table(
        "trend_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source",
            sa.Enum("ARXIV", "HN", "X", "REDDIT", name="trend_signal_source"),
            nullable=False,
        ),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_trend_signals_keyword_observed_at",
        "trend_signals",
        ["keyword", "observed_at"],
        unique=False,
    )

    op.create_table(
        "semantic_fingerprints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("vector", postgresql.JSONB(), nullable=True),
        sa.Column("text_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("normalized_url", name="uq_semantic_fingerprints_normalized_url"),
    )
    op.create_index(
        "ix_semantic_fingerprints_created_at",
        "semantic_fingerprints",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_semantic_fingerprints_created_at", table_name="semantic_fingerprints")
    op.drop_table("semantic_fingerprints")

    op.drop_index("ix_trend_signals_keyword_observed_at", table_name="trend_signals")
    op.drop_table("trend_signals")

    op.drop_column("sources", "trust_score")

    op.execute("DROP TYPE IF EXISTS trend_signal_source")
