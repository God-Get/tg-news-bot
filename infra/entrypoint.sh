#!/usr/bin/env sh
set -e

cd /app/apps/tg_news_bot
alembic upgrade head
exec python -m tg_news_bot
