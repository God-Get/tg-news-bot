from aiogram import Router

from app.bot.handlers.dev import router as dev_router
from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.moderation import router as moderation_router
from app.bot.handlers.edit_mode import router as edit_router

router = Router()
router.include_router(dev_router)
router.include_router(admin_router)
router.include_router(moderation_router)
router.include_router(edit_router)
