Infra assets for tg-news-bot.

Files:
- Dockerfile: production image for the bot.
- docker-compose.yml: local stack with Postgres.
- entrypoint.sh: runs migrations then starts the bot.

Usage:
- docker compose -f infra/docker-compose.yml up --build
