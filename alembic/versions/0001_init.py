from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("inbox_topic_id", sa.Integer(), nullable=True),
        sa.Column("service_topic_id", sa.Integer(), nullable=True),
        sa.Column("ready_topic_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_topic_id", sa.Integer(), nullable=True),
        sa.Column("published_topic_id", sa.Integer(), nullable=True),
        sa.Column("archive_topic_id", sa.Integer(), nullable=True),
        sa.Column("channel_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bot_settings_group_chat_id"), "bot_settings", ["group_chat_id"], unique=False)

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("topic_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("post_message_id", sa.BigInteger(), nullable=True),  # ✅ NEW
        sa.Column("published_message_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("title_en", sa.Text(), nullable=True),
        sa.Column("post_text_en", sa.Text(), nullable=True),
        sa.Column("post_text_ru", sa.Text(), nullable=True),
        sa.Column("summary_ru", sa.Text(), nullable=True),
        sa.Column("has_image", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tg_image_file_id", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_drafts_state"), "drafts", ["state"], unique=False)
    op.create_index(op.f("ix_drafts_group_chat_id"), "drafts", ["group_chat_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_drafts_group_chat_id"), table_name="drafts")
    op.drop_index(op.f("ix_drafts_state"), table_name="drafts")
    op.drop_table("drafts")

    op.drop_index(op.f("ix_bot_settings_group_chat_id"), table_name="bot_settings")
    op.drop_table("bot_settings")
