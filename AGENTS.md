# AGENTS.md

Этот файл задает единые правила для людей и AI-агентов, которые развивают `tg-news-bot`.

## 1. Миссия
- Держать стабильный Telegram workflow для научно-технологических новостей.
- Автоматизировать сбор и подготовку, но оставлять обязательную ручную модерацию перед публикацией.
- Развивать MVP без ломающих изменений.

## 2. Границы системы
- Бизнес-логика и state machine: `apps/tg_news_bot`.
- Telegram transport: только через `PublisherAdapter`.
- Низкоуровневые Telegram API-вызовы: `libs/telegram_publisher`.
- Инфраструктура и запуск: `infra/`.

## 3. Неприкосновенные инварианты
- Один draft = два сообщения в topic:
1. `POST` (основной, с inline-кнопками).
2. `CARD` (информационный статус).
- Кнопки модерации всегда под `POST`.
- Переходы по состояниям только через `DraftWorkflowService`.
- Состояния: `INBOX`, `EDITING`, `READY`, `SCHEDULED`, `PUBLISHED`, `ARCHIVE`.
- При move между topics создается новая пара `POST+CARD`, старая удаляется.

## 4. Команды (должны оставаться рабочими)
- Настройка: `/commands`, `/status`, `/set_group`, `/set_inbox_topic`, `/set_service_topic`, `/set_ready_topic`, `/set_scheduled_topic`, `/set_published_topic`, `/set_archive_topic`, `/set_channel <channel_id>`.
- Источники: `/add_source <rss_url> [name]`, `/list_sources`, `/set_source_topics <source_id> <topics>`, `/clear_source_topics <source_id>`, `/set_source_ssl_insecure <source_id> <on|off>`, `/enable_source <source_id>`, `/disable_source <source_id>`, `/remove_source <source_id>`.
- Ingestion: `/ingest_now`, `/ingest_source <source_id>`, `/ingest_url <article_url> [source_id]`.
- Обработка текста: `/process_range <from_id> <to_id>`.
- Редактирование: `/cancel`.

## 5. Правила внесения изменений
- Любое изменение state/UX проверять на полном цикле:
1. `INBOX -> EDITING -> READY`
2. `READY -> SCHEDULED -> PUBLISHED`
3. `READY -> PUBLISH_NOW -> PUBLISHED`
4. Любое состояние -> `ARCHIVE`
- Изменения БД только через Alembic миграции.
- Новую бизнес-логику покрывать тестами минимум на happy path + 1 failure path.
- При изменении команд обновлять `/commands` и тесты команд.
- При изменении текста поста сохранять очистку служебного мусора (`Date/Source/Summary` и аналоги).

## 6. Definition of Done для задачи
- Код реализован и покрыт тестами.
- `pytest` зеленый.
- В Docker-окружении бот стартует и проходит smoke-check.
- Документация обновлена (`docs/PROJECT_PROMPT.md`, `README.md` при необходимости).
- Изменения закоммичены и отправлены в `main` (или feature branch по договоренности).

## 7. Минимальные операционные команды
- Старт/пересборка: `docker compose -f infra/docker-compose.yml up -d --build`.
- Логи бота: `docker compose -f infra/docker-compose.yml logs bot --tail=200`.
- Проверка контейнеров: `docker compose -f infra/docker-compose.yml ps`.

## 8. Ближайший бэклог
- Команды управления failed scheduled-публикациями (list/retry/cancel).
- Trust score источников и доменов для скоринга.
- Семантический dedup (near-duplicates по embedding).
- Метрики и дашборды воронки ingestion -> publish.
- Авто-теги/рубрикатор и content safety перед READY.
- Runbook восстановления и backup/restore Postgres.
- Улучшенный image filtering (анти-логотип, качество).
- Модуль тренд-аналитики и рекомендаций источников.
- Multi-admin роли и ACL: отложено, пока не завершены пункты выше.
