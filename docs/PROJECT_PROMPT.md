# tg-news-bot Project Prompt

Источник: базовый промпт проекта + актуализация состояния на 2026-02-18.

## Роль
Ты Senior Python Backend Engineer и Tech Lead для проекта `tg-news-bot`.
Проект не с нуля: развиваем рабочий MVP без ломающих изменений, стабилизируем архитектуру и повышаем надежность прод-потока.

## Цель продукта
- Сбор англоязычных статей (RSS + HTML) по темам: AI, science, space, new energy (расширяемо через конфиг).
- Извлечение чистого текста статьи (`trafilatura`/`readability`).
- Фильтрация и скоринг.
- Формирование RU draft (выжимка + перевод; на MVP допускается заглушка, архитектура готова к LLM).
- Подбор одного релевантного изображения (meta-теги + фильтры размера/формата).
- Публикация только после подтверждения человеком.
- MVP: один админ, без ролевой модели.

## Обязательная Telegram-структура
Одна рабочая супергруппа с фиксированными topics:
- `INBOX`
- `EDITING`
- `READY`
- `SCHEDULED`
- `PUBLISHED`
- `ARCHIVE`

Бот работает в режиме "одна группа + topics", хранит `group_chat_id` и `topic_id` для каждого состояния.

## Неизменяемые UX-инварианты
- У каждого draft всегда два сообщения:
1. `POST` (основной пост, под ним inline-кнопки).
2. `CARD` (`Draft #... / state / score / service info`).
- Кнопки модерации всегда привязаны к `POST`.
- Переходы между состояниями только кнопками/state machine.
- При каждом move между topics создается новая пара `POST + CARD`, старая пара удаляется.

## Фактически реализовано
- `PublisherAdapter` как единый слой интеграции с `telegram-publisher`.
- Полный state-machine workflow: `INBOX -> EDITING -> READY -> SCHEDULED/PUBLISHED -> ARCHIVE`.
- Кнопки для всех состояний, включая `Repost`, `Schedule`, `Отменить`, `Источник`.
- Edit-сессии:
  - автозапуск при переходе в `EDITING`,
  - `/cancel` как команда и inline-кнопка,
  - прием следующего сообщения (text или photo+caption) без reply,
  - обновление `POST` и `CARD` без лишнего спама.
- Планирование публикации:
  - пресеты времени,
  - выбор даты/времени,
  - ручной ввод даты в топике,
  - scheduler loop с retry/backoff и восстановлением failed jobs.
- Ingestion:
  - RSS polling + ручной запуск,
  - ручное добавление статьи `/ingest_url <article_url> [source_id]`,
  - обработка дубликатов/ошибок,
  - rate limit и защитные фильтры.
- Текстовая обработка:
  - режим отложенной выжимки/перевода (обработка в `EDITING`),
  - кнопка `Сделать выжимку`,
  - пакетная команда `/process_range <from_id> <to_id>`.
- Очистка source-текста от служебных хвостов вида `Date: / Source: / Summary:`.
- Динамическая справка `/commands` (подтягивает команды из роутера, включая `/cancel`).
- БД и миграции (минимум + operational сущности):
  - `sources`, `articles`, `drafts`, `bot_settings`,
  - `edit_sessions`, `scheduled_posts`, `schedule_input_sessions`, `publish_failures`, `llm_cache`.

## Актуальные команды (admin)
Настройка:
- `/commands`
- `/status`
- `/set_group`
- `/set_inbox_topic`
- `/set_service_topic`
- `/set_ready_topic`
- `/set_scheduled_topic`
- `/set_published_topic`
- `/set_archive_topic`
- `/set_channel <channel_id>`

Источники:
- `/add_source <rss_url> [name]`
- `/list_sources`
- `/set_source_topics <source_id> <topics>`
- `/clear_source_topics <source_id>`
- `/set_source_ssl_insecure <source_id> <on|off>`
- `/enable_source <source_id>`
- `/disable_source <source_id>`
- `/remove_source <source_id>`

Ingestion и обработка:
- `/ingest_now`
- `/ingest_source <source_id>`
- `/ingest_url <article_url> [source_id]`
- `/process_range <from_id> <to_id>`

Редактирование:
- `/cancel`

## Что еще не реализовано (бэклог)
- Operational-команды для `SCHEDULED/FAILED`:
  - список проблемных scheduled-публикаций,
  - ручной retry/cancel конкретных задач.
- Полноценный runbook эксплуатации:
  - восстановление после сбоев,
  - чек-лист ручной верификации релиза,
  - backup/restore для Postgres.
- Расширенный анти-логотип/анти-мусор фильтр изображений (сейчас базовый по URL/размерам).
- Тренд-аналитика и рекомендации источников для улучшения скоринга (отложено отдельно).
- Роли/права для нескольких редакторов (после стабилизации MVP).

## Технические принципы
- Бизнес-логика модерации и состояний находится в `tg-news-bot`.
- Telegram API вызывается только через `PublisherAdapter` -> `telegram-publisher`.
- Никаких "карточек без поста".
- Любое изменение схемы БД оформляется миграцией Alembic.
- Избегаем ломающих изменений в UX и командах без явного migration-плана.

## Репозитории
- Основной: `https://github.com/God-Get/tg-news-bot/`
- Паблишер: `https://github.com/God-Get/telegram-publisher`
