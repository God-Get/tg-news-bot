from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tg_news_bot.ports.publisher import PublisherNotFound
from tg_news_bot.repositories.bot_settings import BotSettingsRepository
from tg_news_bot.telegram.handlers.editing import EditContext, create_edit_router


class _BeginContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _Session:
    def begin(self) -> _BeginContext:
        return _BeginContext()


class _SessionFactory:
    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


@dataclass
class _EditSessionsSpy:
    cancel_calls: list[tuple[int, int]] = field(default_factory=list)
    payloads: list[object] = field(default_factory=list)

    async def cancel_active_for_topic(
        self,
        session,  # noqa: ANN001
        *,
        group_chat_id: int,
        topic_id: int,
    ) -> None:
        self.cancel_calls.append((group_chat_id, topic_id))

    async def apply_edit(self, session, payload) -> None:  # noqa: ANN001
        self.payloads.append(payload)


@dataclass
class _PublisherSpy:
    delete_calls: list[tuple[int, int]] = field(default_factory=list)
    raise_not_found: bool = False

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.delete_calls.append((chat_id, message_id))
        if self.raise_not_found:
            raise PublisherNotFound("not found")


def _message(
    *,
    user_id: int = 10,
    chat_id: int = -1001,
    topic_id: int | None = 12,
    message_id: int = 100,
    text: str | None = None,
    photo: list | None = None,
    caption: str | None = None,
):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id),
        message_thread_id=topic_id,
        message_id=message_id,
        text=text,
        photo=photo,
        caption=caption,
    )


def _get_handlers(context: EditContext):
    router = create_edit_router(context)
    cancel_handler = router.message.handlers[0].callback
    edit_handler = router.message.handlers[1].callback
    return cancel_handler, edit_handler


@pytest.mark.asyncio
async def test_cancel_command_in_editing_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=12)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy()
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    cancel_handler, _ = _get_handlers(context)

    await cancel_handler(_message(text="/cancel", message_id=200))

    assert edit_sessions.cancel_calls == [(-1001, 12)]
    assert publisher.delete_calls == [(-1001, 200)]


@pytest.mark.asyncio
async def test_cancel_command_handles_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=12)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy(raise_not_found=True)
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    cancel_handler, _ = _get_handlers(context)

    await cancel_handler(_message(text="/cancel", message_id=201))

    assert edit_sessions.cancel_calls == [(-1001, 12)]
    assert publisher.delete_calls == [(-1001, 201)]


@pytest.mark.asyncio
async def test_cancel_command_outside_editing_topic_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=99)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy()
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    cancel_handler, _ = _get_handlers(context)

    await cancel_handler(_message(text="/cancel", topic_id=12))

    assert edit_sessions.cancel_calls == []
    assert publisher.delete_calls == []


@pytest.mark.asyncio
async def test_text_message_creates_edit_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=12)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy()
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    _, edit_handler = _get_handlers(context)

    await edit_handler(_message(text="new text", message_id=301))

    assert len(edit_sessions.payloads) == 1
    payload = edit_sessions.payloads[0]
    assert payload.chat_id == -1001
    assert payload.topic_id == 12
    assert payload.user_id == 10
    assert payload.message_id == 301
    assert payload.text == "new text"
    assert payload.photo_file_id is None
    assert payload.photo_unique_id is None


@pytest.mark.asyncio
async def test_photo_message_creates_edit_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=12)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy()
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    _, edit_handler = _get_handlers(context)

    photo = [SimpleNamespace(file_id="file_1", file_unique_id="uniq_1")]
    await edit_handler(_message(photo=photo, caption="caption text", message_id=302))

    assert len(edit_sessions.payloads) == 1
    payload = edit_sessions.payloads[0]
    assert payload.text == "caption text"
    assert payload.photo_file_id == "file_1"
    assert payload.photo_unique_id == "uniq_1"


@pytest.mark.asyncio
async def test_non_admin_edit_message_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_or_create(self, session):  # noqa: ANN001, ARG001
        return SimpleNamespace(group_chat_id=-1001, editing_topic_id=12)

    monkeypatch.setattr(BotSettingsRepository, "get_or_create", fake_get_or_create)
    publisher = _PublisherSpy()
    edit_sessions = _EditSessionsSpy()
    context = EditContext(
        settings=SimpleNamespace(admin_user_id=10),
        session_factory=_SessionFactory(),
        edit_sessions=edit_sessions,
        publisher=publisher,
    )
    _, edit_handler = _get_handlers(context)

    await edit_handler(_message(user_id=999, text="ignored"))

    assert edit_sessions.payloads == []
