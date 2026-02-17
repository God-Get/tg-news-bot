"""Add schedule input sessions

Revision ID: 0003_schedule_input_sessions
Revises: 0002_llm_cache
Create Date: 2026-02-17 02:25:00

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_schedule_input_sessions"
down_revision = "0002_llm_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_input_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id"), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "COMPLETED", "CANCELLED", "EXPIRED", name="schedule_input_status"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_index(
        "uq_schedule_input_active_draft",
        "schedule_input_sessions",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index("uq_schedule_input_active_draft", table_name="schedule_input_sessions")
    op.drop_table("schedule_input_sessions")
    op.execute("DROP TYPE IF EXISTS schedule_input_status")
