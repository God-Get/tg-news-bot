from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.db.base import Base
from app.db import models  # noqa: F401  (регистрирует модели)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_sync_db_url() -> str:
    """
    App: postgresql+asyncpg://...
    Alembic: sync URL -> postgresql://... (нужен psycopg2/psycopg)
    """
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url.replace("+asyncpg", "")


def run_migrations_offline() -> None:
    url = _get_sync_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _get_sync_db_url()
    connectable = create_engine(url, pool_pre_ping=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
