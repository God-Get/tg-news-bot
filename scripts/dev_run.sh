#!/usr/bin/env bash
set -e

cp -n .env.example .env || true

docker compose up -d db
docker compose run --rm bot python -m alembic upgrade head
docker compose up -d bot

echo "OK. Open Telegram and send /ping to your bot, then /status."
