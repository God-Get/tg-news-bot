from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tg_news_bot.db.models import Draft, DraftState
from tg_news_bot.services.workflow import DraftWorkflowService


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _Session:
    def begin(self) -> _AsyncContext:
        return _AsyncContext()

    async def flush(self) -> None:
        return None


class _SessionFactory:
    def __call__(self) -> _SessionFactory:
        return self

    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


@dataclass
class _DraftRepo:
    draft: Draft

    async def get_for_update(self, session, draft_id: int) -> Draft:  # noqa: ANN001, ARG002
        if draft_id != self.draft.id:
            raise LookupError
        return self.draft

    async def get(self, session, draft_id: int) -> Draft | None:  # noqa: ANN001, ARG002
        if draft_id != self.draft.id:
            return None
        return self.draft


@dataclass
class _SourceRepo:
    tags: dict | None = None

    async def get_by_id(self, session, source_id: int):  # noqa: ANN001, ARG002
        if self.tags is None:
            return None
        return SimpleNamespace(tags=self.tags)


@dataclass
class _ArticleRepo:
    extracted_text: str | None = None

    async def get_by_id(self, session, article_id: int):  # noqa: ANN001, ARG002
        if self.extracted_text is None:
            return None
        return SimpleNamespace(extracted_text=self.extracted_text)


@dataclass
class _PublisherSpy:
    edit_post_calls: list[dict] = field(default_factory=list)
    edit_text_calls: list[dict] = field(default_factory=list)

    async def edit_post(self, *, chat_id: int, message_id: int, content, keyboard) -> None:  # noqa: ANN001
        self.edit_post_calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "content": content,
                "keyboard": keyboard,
            }
        )

    async def edit_text(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        keyboard,  # noqa: ANN001
        parse_mode: str | None,
        disable_web_page_preview: bool,
    ) -> None:
        self.edit_text_calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "keyboard": keyboard,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }
        )


@dataclass
class _TextPipelineStub:
    title_ru: str = "Заголовок RU"
    summary_ru: str = "Краткая выжимка RU"
    calls: list[dict] = field(default_factory=list)

    async def generate_parts(
        self,
        *,
        title_en: str | None,
        text_en: str | None,
        topic_hints: list[str] | None = None,
    ):
        self.calls.append(
            {
                "title_en": title_en,
                "text_en": text_en,
                "topic_hints": topic_hints,
            }
        )
        return SimpleNamespace(
            title_ru=self.title_ru,
            summary_ru=self.summary_ru,
        )


def _editing_draft() -> Draft:
    return Draft(
        id=1,
        state=DraftState.EDITING,
        normalized_url="https://example.com/article",
        domain="example.com",
        title_en="English title",
        extracted_text="Original English source text",
        post_text_ru="Raw source text",
        source_id=2,
        group_chat_id=-1001,
        post_message_id=101,
        card_message_id=102,
    )


@pytest.mark.asyncio
async def test_process_editing_text_updates_draft_and_messages() -> None:
    draft = _editing_draft()
    publisher = _PublisherSpy()
    pipeline = _TextPipelineStub(title_ru="RU title", summary_ru="RU summary")
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=publisher,
        draft_repo=_DraftRepo(draft),
        source_repo=_SourceRepo(tags={"topics": ["AI", "Space"]}),
        text_pipeline=pipeline,
    )

    await workflow.process_editing_text(draft_id=1)

    assert draft.post_text_ru == "RU title\n\nRU summary"
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["title_en"] == "English title"
    assert pipeline.calls[0]["text_en"] == "Original English source text"
    assert pipeline.calls[0]["topic_hints"] == ["ai", "space"]
    assert [call["message_id"] for call in publisher.edit_post_calls] == [101]
    assert [call["message_id"] for call in publisher.edit_text_calls] == [102]


@pytest.mark.asyncio
async def test_process_editing_text_rejects_non_editing_state() -> None:
    draft = _editing_draft()
    draft.state = DraftState.READY
    pipeline = _TextPipelineStub()
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=_PublisherSpy(),
        draft_repo=_DraftRepo(draft),
        source_repo=_SourceRepo(tags={"topics": ["ai"]}),
        text_pipeline=pipeline,
    )

    with pytest.raises(ValueError, match="EDITING"):
        await workflow.process_editing_text(draft_id=1)
    assert pipeline.calls == []


@pytest.mark.asyncio
async def test_process_editing_text_requires_source_text() -> None:
    draft = _editing_draft()
    draft.extracted_text = None
    draft.post_text_ru = None
    draft.title_en = None
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=_PublisherSpy(),
        draft_repo=_DraftRepo(draft),
        source_repo=_SourceRepo(tags=None),
        text_pipeline=_TextPipelineStub(),
    )

    with pytest.raises(ValueError, match="source text"):
        await workflow.process_editing_text(draft_id=1)


@pytest.mark.asyncio
async def test_process_editing_text_requires_pipeline() -> None:
    draft = _editing_draft()
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=_PublisherSpy(),
        draft_repo=_DraftRepo(draft),
        source_repo=_SourceRepo(tags={"topics": ["ai"]}),
    )

    with pytest.raises(RuntimeError, match="text pipeline"):
        await workflow.process_editing_text(draft_id=1)


@pytest.mark.asyncio
async def test_process_editing_text_prefers_article_source_over_post_text() -> None:
    draft = _editing_draft()
    draft.extracted_text = None
    draft.article_id = 77
    draft.post_text_ru = "Edited post text should not be source"
    publisher = _PublisherSpy()
    pipeline = _TextPipelineStub()
    workflow = DraftWorkflowService(
        session_factory=_SessionFactory(),
        publisher=publisher,
        draft_repo=_DraftRepo(draft),
        source_repo=_SourceRepo(tags={"topics": ["AI"]}),
        article_repo=_ArticleRepo(extracted_text="Article source text"),
        text_pipeline=pipeline,
    )

    await workflow.process_editing_text(draft_id=1)

    assert len(pipeline.calls) == 1
    assert pipeline.calls[0]["text_en"] == "Article source text"
