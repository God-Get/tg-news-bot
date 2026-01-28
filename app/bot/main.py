from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.bot.middlewares.db import DbSessionMiddleware
from app.workers.scheduler import run_scheduler
from app.bot.router import router
from app.core.config import settings
from app.core.logging import log, setup_logging


async def main() -> None:
    setup_logging(logging.INFO)
    log.info("starting_bot")

    # Без parse_mode по умолчанию (иначе ломается на "_" в статусе и т.п.)
    bot = Bot(token=settings.bot_token)

    dp = Dispatcher()

    # надежно для aiogram 3.7+
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())

    dp.include_router(router)
    asyncio.create_task(run_scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
