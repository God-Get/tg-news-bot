FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install local libs and app in editable mode.
COPY libs/telegram_publisher /app/libs/telegram_publisher
COPY apps/tg_news_bot /app/apps/tg_news_bot
COPY infra/entrypoint.sh /app/infra/entrypoint.sh

RUN pip install --no-cache-dir -e /app/libs/telegram_publisher -e /app/apps/tg_news_bot \
    && chmod +x /app/infra/entrypoint.sh

ENTRYPOINT ["/app/infra/entrypoint.sh"]
