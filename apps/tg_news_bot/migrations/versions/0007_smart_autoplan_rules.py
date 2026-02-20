"""Add bot_settings.autoplan_rules for Smart Scheduler

Revision ID: 0007_smart_autoplan_rules
Revises: 0006_trend_discovery_candidates
Create Date: 2026-02-20 09:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_smart_autoplan_rules"
down_revision = "0006_trend_discovery_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column("autoplan_rules", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_settings", "autoplan_rules")

