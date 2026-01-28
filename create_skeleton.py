# create_skeleton.py
from pathlib import Path

ROOT = Path(".").resolve()

DIRS = [
    "app",
    "app/bot",
    "app/bot/handlers",
    "app/bot/keyboards",
    "app/bot/middlewares",
    "app/bot/states",
    "app/bot/utils",
    "app/core",
    "app/db",
    "app/db/models",
    "app/db/repos",
    "app/services",
    "app/services/fetchers",
    "app/services/llm",
    "app/services/media",
    "app/services/moderation",
    "app/services/publishing",
    "app/services/scoring",
    "app/services/scheduling",
    "app/workers",
    "alembic",
    "alembic/versions",
    "docker",
    "scripts",
    "tests",
]

FILES = [
    # root
    ".env.example",
    ".gitignore",
    "README.md",
    "Makefile",
    "pyproject.toml",
    "docker-compose.yml",

    # app
    "app/__init__.py",

    # core
    "app/core/__init__.py",
    "app/core/config.py",
    "app/core/logging.py",
    "app/core/constants.py",

    # bot
    "app/bot/__init__.py",
    "app/bot/main.py",
    "app/bot/router.py",

    # bot handlers (commands + callbacks)
    "app/bot/handlers/__init__.py",
    "app/bot/handlers/admin.py",         # /set_group, /set_*_topic, /set_channel, /status
    "app/bot/handlers/sources.py",       # /add_source, /list_sources, enable/disable
    "app/bot/handlers/fetch.py",         # /fetch, /set_limit, /fetch_debug, /reset_dedup
    "app/bot/handlers/moderation.py",    # кнопки переходов по стейтам
    "app/bot/handlers/edit_mode.py",     # ✏️ Edit flow (reply-to-message)

    # bot keyboards
    "app/bot/keyboards/__init__.py",
    "app/bot/keyboards/inline.py",       # инлайн-кнопки по стейтам + schedule shortcuts

    # bot middlewares
    "app/bot/middlewares/__init__.py",
    "app/bot/middlewares/db.py",         # прокидывание DB session
    "app/bot/middlewares/config.py",

    # bot states
    "app/bot/states/__init__.py",
    "app/bot/states/fsm.py",             # ожидание текста/времени/прочее

    # bot utils
    "app/bot/utils/__init__.py",
    "app/bot/utils/formatting.py",       # сборка RU поста (title/intro/bullets/hashtags)
    "app/bot/utils/validators.py",       # длина 500–900, заголовок <=80, т.д.
    "app/bot/utils/callback_data.py",    # схемы callback_data
    "app/bot/utils/telegram_ops.py",     # move/update карточек по topic_id/message_id

    # db
    "app/db/__init__.py",
    "app/db/session.py",
    "app/db/base.py",

    # db models (таблицы MVP)
    "app/db/models/__init__.py",
    "app/db/models/source.py",
    "app/db/models/article.py",
    "app/db/models/draft.py",
    "app/db/models/settings.py",         # group_chat_id + topic_ids + channel_id

    # db repos
    "app/db/repos/__init__.py",
    "app/db/repos/sources.py",
    "app/db/repos/articles.py",
    "app/db/repos/drafts.py",
    "app/db/repos/settings.py",

    # services: fetching / extraction / dedup / scoring / llm / media / publishing / scheduling
    "app/services/__init__.py",

    "app/services/fetchers/__init__.py",
    "app/services/fetchers/rss.py",
    "app/services/fetchers/html.py",

    "app/services/extractor.py",         # trafilatura/readability wrapper
    "app/services/url_normalizer.py",    # убрать UTM и нормализация
    "app/services/dedup.py",             # url/title sim/text hash
    "app/services/scoring/__init__.py",
    "app/services/scoring/scorer.py",    # whitelist/blacklist/min length/reasons/score

    "app/services/llm/__init__.py",
    "app/services/llm/client.py",        # OpenAI клиент (позже)
    "app/services/llm/prompt.py",        # JSON schema rules (позже)
    "app/services/llm/parser.py",        # строгий парс/валидация JSON

    "app/services/media/__init__.py",
    "app/services/media/image_finder.py",# og:image/twitter:image/эвристики
    "app/services/media/filters.py",     # logo/ad/min 600px etc.
    "app/services/media/telegram_cache.py", # tg_file_id/unique_id reuse

    "app/services/moderation/__init__.py",
    "app/services/moderation/card_renderer.py", # карточка + кнопки, состояние
    "app/services/moderation/state_machine.py", # INBOX/EDITING/READY/...

    "app/services/publishing/__init__.py",
    "app/services/publishing/channel_publisher.py", # sendPhoto/sendMessage
    "app/services/publishing/post_link.py",          # ссылка на опубликованный пост

    "app/services/scheduling/__init__.py",
    "app/services/scheduling/scheduler.py", # постановка/отмена/изменение времени

    # workers (если будем запускать cron/apscheduler позже)
    "app/workers/__init__.py",
    "app/workers/fetch_worker.py",
    "app/workers/schedule_worker.py",

    # migrations (alembic)
    "alembic/env.py",
    "alembic/script.py.mako",
    "alembic/versions/__init__.py",
    "alembic.ini",

    # docker
    "docker/Dockerfile",
    "docker/app.Dockerfile",

    # scripts
    "scripts/dev_run.sh",
    "scripts/db_migrate.sh",

    # tests
    "tests/__init__.py",
]

def ensure_dirs():
    for d in DIRS:
        (ROOT / d).mkdir(parents=True, exist_ok=True)

def ensure_files():
    for f in FILES:
        p = ROOT / f
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.touch()

def main():
    ensure_dirs()
    ensure_files()
    print("OK: skeleton created.")

if __name__ == "__main__":
    main()
