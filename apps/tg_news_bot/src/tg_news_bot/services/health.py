"""Health and metrics HTTP server."""

from __future__ import annotations

from aiohttp import web

from tg_news_bot.config import HealthSettings
from tg_news_bot.logging import get_logger
from tg_news_bot.services.metrics import metrics


class HealthServer:
    def __init__(self, settings: HealthSettings) -> None:
        self._settings = settings
        self._runner: web.AppRunner | None = None
        self._log = get_logger(__name__)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/metrics", self._handle_metrics)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self._settings.host, port=self._settings.port)
        await site.start()
        self._runner = runner
        self._log.info("health_server_started", host=self._settings.host, port=self._settings.port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    @staticmethod
    async def _handle_health(request: web.Request) -> web.Response:
        return web.Response(text="ok")

    @staticmethod
    async def _handle_metrics(request: web.Request) -> web.Response:
        body = metrics.render()
        return web.Response(text=body, content_type="text/plain; version=0.0.4")
