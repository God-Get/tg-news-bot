"""Add publish failure DLQ and scheduler retry fields

Revision ID: 0004_publish_failures
Revises: 0003_schedule_input_sessions
Create Date: 2026-02-17 03:30:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_publish_failures"
down_revision = "0003_schedule_input_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_posts",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scheduled_posts",
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "scheduled_posts",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "publish_failures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("scheduled_post_id", sa.Integer(), sa.ForeignKey("scheduled_posts.id"), nullable=True),
        sa.Column(
            "context",
            sa.Enum("SCHEDULED", "MANUAL", name="publish_failure_context"),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_publish_failures_draft_resolved",
        "publish_failures",
        ["draft_id", "resolved"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_publish_failures_draft_resolved", table_name="publish_failures")
    op.drop_table("publish_failures")

    op.drop_column("scheduled_posts", "next_retry_at")
    op.drop_column("scheduled_posts", "last_error")
    op.drop_column("scheduled_posts", "attempts")

    op.execute("DROP TYPE IF EXISTS publish_failure_context")
