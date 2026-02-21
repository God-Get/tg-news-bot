from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from tg_news_bot.services.trend_discovery import TrendDiscoveryService


class _Response:
    def __init__(self, url: str, text: str, status_code: int = 200) -> None:
        self.url = url
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        request = httpx.Request("GET", self.url)
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError(
            f"HTTP {self.status_code}",
            request=request,
            response=response,
        )


class _HTTPFake:
    def __init__(self, payloads: dict[str, _Response | Exception]) -> None:
        self._payloads = payloads

    async def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        payload = self._payloads[url]
        if isinstance(payload, Exception):
            raise payload
        return payload


class _DummyAsyncSession:
    async def __aenter__(self):  # noqa: ANN201
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN201
        return False

    def begin(self):  # noqa: ANN201
        return self


def _dummy_session_factory():  # noqa: ANN201
    return _DummyAsyncSession()


def _make_service() -> TrendDiscoveryService:
    settings = SimpleNamespace(
        trend_discovery=SimpleNamespace(
            ai_enrichment=False,
            github_trending_enabled=True,
            github_trending_url="https://github.com/trending",
            steam_charts_enabled=True,
            steam_charts_url="https://steamcharts.com/top",
            boxoffice_enabled=True,
            boxoffice_urls=["https://www.boxofficemojo.com/month/february/2026/"],
        ),
        internet_scoring=SimpleNamespace(),
        trends=SimpleNamespace(),
        llm=SimpleNamespace(enabled=False, provider="openai_compat", api_key=None),
    )
    return TrendDiscoveryService(
        settings=settings,
        session_factory=_dummy_session_factory,
        publisher=None,
        ingestion_runner=None,
        internet_scoring=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_collect_github_trending_parses_items() -> None:
    service = _make_service()
    html = """
    <article class="Box-row">
      <h2><a href="/openai/openai-python">openai/openai-python</a></h2>
      <p>Official Python library for OpenAI APIs.</p>
    </article>
    """
    http = _HTTPFake(
        {
            "https://github.com/trending": _Response(
                "https://github.com/trending",
                html,
                status_code=200,
            )
        }
    )
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    rows = await service._collect_github_trending(http, since, 5)  # noqa: SLF001

    assert len(rows) == 1
    assert rows[0].source_name == "GITHUB"
    assert rows[0].domain == "github.com"
    assert rows[0].url == "https://github.com/openai/openai-python"


@pytest.mark.asyncio
async def test_collect_steam_charts_parses_items() -> None:
    service = _make_service()
    html = """
    <table>
      <tr><td><a href="/app/730">Counter-Strike 2</a></td></tr>
      <tr><td><a href="/app/570">Dota 2</a></td></tr>
    </table>
    """
    http = _HTTPFake(
        {
            "https://steamcharts.com/top": _Response(
                "https://steamcharts.com/top",
                html,
                status_code=200,
            )
        }
    )
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    rows = await service._collect_steam_charts(http, since, 5)  # noqa: SLF001

    assert len(rows) == 2
    assert rows[0].source_name == "STEAM_CHARTS"
    assert rows[0].domain == "store.steampowered.com"
    assert rows[0].url == "https://store.steampowered.com/app/730/"


@pytest.mark.asyncio
async def test_collect_boxoffice_returns_empty_on_fetch_failure() -> None:
    service = _make_service()
    request = httpx.Request("GET", "https://www.boxofficemojo.com/month/february/2026/")
    response = httpx.Response(status_code=503, request=request)
    http = _HTTPFake(
        {
            "https://www.boxofficemojo.com/month/february/2026/": httpx.HTTPStatusError(
                "503",
                request=request,
                response=response,
            )
        }
    )
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    rows = await service._collect_boxoffice(http, since, 5)  # noqa: SLF001

    assert rows == []
