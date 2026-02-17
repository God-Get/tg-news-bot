# tg-news-bot monorepo

This repo contains:
- apps/tg_news_bot: Telegram bot
- libs/telegram_publisher: Telegram API wrapper
- infra: Docker and deployment assets

## Useful env settings

Text generation:
- `TEXT_GENERATION__SUMMARY_MAX_CHARS=900`
- `TEXT_GENERATION__KEEP_LANG_PREFIX=false`

LLM provider (OpenAI-compatible API):
- `LLM__ENABLED=false`
- `LLM__PROVIDER=openai_compat`
- `LLM__API_KEY=<your_api_key>`
- `LLM__BASE_URL=https://api.openai.com/v1`
- `LLM__MODEL=gpt-4o-mini`
- `LLM__TIMEOUT_SECONDS=30`
- `LLM__TEMPERATURE=0.2`
- `LLM__MAX_RETRIES=2`
- `LLM__RETRY_BACKOFF_SECONDS=1`
- `LLM__CIRCUIT_BREAKER_THRESHOLD=5`
- `LLM__CIRCUIT_BREAKER_COOLDOWN_SECONDS=120`

Scheduler hardening:
- `SCHEDULER__MAX_PUBLISH_ATTEMPTS=3`
- `SCHEDULER__RETRY_BACKOFF_SECONDS=60`
- `SCHEDULER__RECOVER_FAILED_AFTER_SECONDS=300`

RSS hardening:
- `RSS__PER_SOURCE_MIN_INTERVAL_SECONDS=0`
- `RSS__REQUEST_DELAY_SECONDS=0.0`
- `RSS__DEDUP_TITLE_WINDOW_HOURS=72`
- `RSS__ALLOW_INSECURE_SSL_FALLBACK=false`
- `RSS__INSECURE_SSL_DOMAINS=["example.com"]`
- `RSS__BLOCKED_DOMAINS=["example.com"]`
- `RSS__BLOCKED_URL_KEYWORDS=["/sponsored/","utm_medium=ad"]`
- `RSS__BLOCKED_TITLE_KEYWORDS=["podcast","newsletter","sponsored"]`

Scoring tuning:
- `SCORING__TITLE_KEYWORD_MULTIPLIER=1.3`

Post formatting:
- `POST_FORMATTING__SECTIONS_ORDER=title,body,hashtags,source`
- `POST_FORMATTING__HASHTAGS_LIMIT=5`
- `POST_FORMATTING__FALLBACK_HASHTAG=news`
- `POST_FORMATTING__SOURCE_LABEL=Источник`
- `POST_FORMATTING__SOURCE_MODE=button` (`text|button|both`)
- `POST_FORMATTING__DISCUSSION_URL=https://t.me/<your_group_or_topic_link>`
- `POST_FORMATTING__DISCUSSION_LABEL=Обсудить`
- `POST_FORMATTING__SECTION_SEPARATOR=\n\n`

Source topics for prompt tuning:
- in `sources.tags` set `{\"topics\": [\"ai\", \"science\", \"space\", \"energy\"]}`
- or use bot commands:
- `/set_source_topics <source_id> ai,science`
- `/clear_source_topics <source_id>`
- `/set_source_ssl_insecure <source_id> on|off`

Schedule UX:
- presets / date+time buttons / manual input in topic
- manual format: `ДД.ММ.ГГГГ ЧЧ:ММ` or `YYYY-MM-DD HH:MM`
- timezone is shown in schedule menu (`TZ: ...`)
- for `today`, past slots are hidden in time picker
- if selected time is already in the past, bot asks to choose another slot
- in `SCHEDULED` card, planned publish datetime is always shown

## Quick manual check (Telegram)

1. Create or ingest a draft into `INBOX`.
2. Move draft to `READY` and press `Publish сейчас`.
3. Verify the channel post:
- contains `title + body + hashtags`
- has source as inline button (`Источник`)
- does not contain raw source URL in text (when `POST_FORMATTING__SOURCE_MODE=button`).
