"""Database package."""

from tg_news_bot.db.base import Base
from tg_news_bot.db import models as models

__all__ = ["Base", "models"]
