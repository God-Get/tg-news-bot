"""Monitoring setup helpers."""

from __future__ import annotations

from collections.abc import Mapping

from tg_news_bot.logging import get_logger


def configure_sentry(*, dsn: str | None) -> None:
    if not dsn:
        return

    try:
        import sentry_sdk
    except ModuleNotFoundError:
        get_logger(__name__).warning("sentry_sdk_not_installed")
        return

    sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
    get_logger(__name__).info("sentry_initialized")


def add_sentry_breadcrumb(
    *,
    category: str,
    message: str,
    level: str = "info",
    data: Mapping[str, object] | None = None,
) -> None:
    try:
        import sentry_sdk
    except ModuleNotFoundError:
        return
    sentry_sdk.add_breadcrumb(
        category=category,
        message=message,
        level=level,
        data=dict(data or {}),
    )


def capture_sentry_exception(exc: Exception, *, context: Mapping[str, object] | None = None) -> None:
    try:
        import sentry_sdk
    except ModuleNotFoundError:
        return
    with sentry_sdk.push_scope() as scope:
        if context:
            for key, value in context.items():
                scope.set_extra(str(key), value)
        sentry_sdk.capture_exception(exc)
