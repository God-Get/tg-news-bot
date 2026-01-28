#!/usr/bin/env bash
set -e

docker compose run --rm bot python -m alembic upgrade head
echo "OK: migrated"
