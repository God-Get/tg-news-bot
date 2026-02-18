from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from tg_news_bot.config import LLMSettings, TextGenerationSettings
from tg_news_bot.services.text_generation import (
    LLMCircuitOpenError,
    LLMTranslator,
    LLMSummarizer,
    OpenAICompatClient,
    StubSummarizer,
    StubTranslator,
    TextPipeline,
    build_text_pipeline,
)


@dataclass
class _FakeClient:
    response: str
    system_prompts: list[str] = field(default_factory=list)
    user_prompts: list[str] = field(default_factory=list)

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        return self.response


@dataclass
class _FakeSequenceClient:
    responses: list[str]
    system_prompts: list[str] = field(default_factory=list)
    user_prompts: list[str] = field(default_factory=list)

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_prompts.append(user_prompt)
        if not self.responses:
            return ""
        return self.responses.pop(0)


class _SequenceClient(OpenAICompatClient):
    def __init__(self, sequence: list[object], **kwargs) -> None:
        super().__init__(**kwargs)
        self._sequence = sequence
        self.calls = 0

    async def _request_completion(self, *, system_prompt: str, user_prompt: str) -> str:  # noqa: ARG002
        self.calls += 1
        item = self._sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)


@dataclass
class _OrderSummarizer:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def summarize(self, text: str, *, topic_hints=None) -> str:  # noqa: ANN001
        self.calls.append(("summarize", text))
        return "summary-en"


@dataclass
class _OrderTranslator:
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def translate(self, text: str, *, target_lang: str) -> str:
        self.calls.append((target_lang, text))
        return f"ru:{text}"


@pytest.mark.asyncio
async def test_generate_post_with_title() -> None:
    pipeline = TextPipeline(StubSummarizer(max_chars=30), StubTranslator())

    result = await pipeline.generate_post(
        title_en="Hello",
        text_en="Sentence one. Sentence two is longer.",
    )

    assert result == "Hello\n\nSentence one."


@pytest.mark.asyncio
async def test_generate_post_without_title() -> None:
    pipeline = TextPipeline(StubSummarizer(max_chars=100), StubTranslator())

    result = await pipeline.generate_post(title_en=None, text_en="Hello")

    assert result == "Hello"


@pytest.mark.asyncio
async def test_pipeline_runs_summary_before_translation() -> None:
    summarizer = _OrderSummarizer()
    translator = _OrderTranslator()
    pipeline = TextPipeline(summarizer, translator)

    result = await pipeline.generate_post(
        title_en="Breaking Title",
        text_en="Main body text",
    )

    assert summarizer.calls == [("summarize", "Main body text")]
    assert translator.calls == [("RU", "summary-en"), ("RU", "Breaking Title")]
    assert result == "ru:Breaking Title\n\nru:summary-en"


@pytest.mark.asyncio
async def test_translator_can_keep_lang_prefix() -> None:
    pipeline = TextPipeline(
        StubSummarizer(max_chars=100),
        StubTranslator(keep_lang_prefix=True),
    )

    result = await pipeline.generate_post(title_en="Hello", text_en="World")

    assert result == "[RU] Hello\n\n[RU] World"


@pytest.mark.asyncio
async def test_llm_summarizer_respects_limit() -> None:
    fake = _FakeClient(response="x" * 200)
    summarizer = LLMSummarizer(client=fake, max_chars=50)

    result = await summarizer.summarize("input text")

    assert len(result) <= 53
    assert result.endswith("...")


@pytest.mark.asyncio
async def test_llm_summarizer_uses_topic_hints_in_prompt() -> None:
    fake = _FakeClient(response="summary")
    summarizer = LLMSummarizer(client=fake, max_chars=120)

    await summarizer.summarize("input text", topic_hints=["ai", "space"])

    assert len(fake.system_prompts) == 1
    prompt = fake.system_prompts[0].lower()
    assert "topic hints: ai, space" in prompt
    assert "model type" in prompt
    assert "mission goal" in prompt


@pytest.mark.asyncio
async def test_llm_translator_prefix_option() -> None:
    translator = LLMTranslator(
        client=_FakeClient(response="Привет мир"),
        keep_lang_prefix=True,
    )

    result = await translator.translate("Hello world", target_lang="RU")

    assert result == "[RU] Привет мир"


@pytest.mark.asyncio
async def test_llm_translator_adds_fact_anchors_to_prompt() -> None:
    fake = _FakeClient(response="Перевод")
    translator = LLMTranslator(client=fake, keep_lang_prefix=False)

    source = (
        "NASA launched Artemis II on 2026-05-21 with GPT-5 budget 2.5B. "
        "Details: https://example.com/mission"
    )
    await translator.translate(source, target_lang="RU")

    assert len(fake.user_prompts) == 2
    first_prompt = fake.user_prompts[0]
    assert "Target language: RU" in first_prompt
    assert "NASA" in first_prompt
    assert "Artemis II" in first_prompt
    assert "2026-05-21" in first_prompt
    assert "GPT-5" in first_prompt
    assert "https://example.com/mission" in first_prompt
    second_prompt = fake.user_prompts[1]
    assert "Current translation" in second_prompt


@pytest.mark.asyncio
async def test_llm_translator_uses_glossary_and_refine_pass() -> None:
    fake = _FakeSequenceClient(responses=["черновой перевод", "отредактированный перевод"])
    translator = LLMTranslator(
        client=fake,
        keep_lang_prefix=False,
        style="journalistic",
        refine_pass=True,
        glossary={"OpenAI": "OpenAI", "inference": "инференс"},
    )

    result = await translator.translate(
        "OpenAI inference quality improved",
        target_lang="RU",
    )

    assert result == "отредактированный перевод"
    assert len(fake.user_prompts) == 2
    assert "Required glossary:" in fake.user_prompts[0]
    assert "- OpenAI -> OpenAI" in fake.user_prompts[0]
    assert "- inference -> инференс" in fake.user_prompts[0]
    assert "Current translation:" in fake.user_prompts[1]
    assert "черновой перевод" in fake.user_prompts[1]


@pytest.mark.asyncio
async def test_openai_client_retries_then_succeeds() -> None:
    timeout_exc = httpx.TimeoutException("timeout")
    client = _SequenceClient(
        sequence=[timeout_exc, "ok"],
        api_key="k",
        base_url="https://example.com/v1",
        model="m",
        timeout_seconds=30,
        temperature=0.2,
        max_retries=1,
        retry_backoff_seconds=0.01,
        circuit_breaker_threshold=5,
        circuit_breaker_cooldown_seconds=60,
    )

    result = await client.complete(system_prompt="s", user_prompt="u")

    assert result == "ok"
    assert client.calls == 2


@pytest.mark.asyncio
async def test_openai_client_circuit_breaker_opens() -> None:
    timeout_exc = httpx.TimeoutException("timeout")
    client = _SequenceClient(
        sequence=[timeout_exc],
        api_key="k",
        base_url="https://example.com/v1",
        model="m",
        timeout_seconds=30,
        temperature=0.2,
        max_retries=0,
        retry_backoff_seconds=0.01,
        circuit_breaker_threshold=1,
        circuit_breaker_cooldown_seconds=60,
    )

    with pytest.raises(httpx.TimeoutException):
        await client.complete(system_prompt="s", user_prompt="u")

    with pytest.raises(LLMCircuitOpenError):
        await client.complete(system_prompt="s", user_prompt="u")


def test_build_text_pipeline_uses_stub_when_llm_disabled() -> None:
    pipeline = build_text_pipeline(
        TextGenerationSettings(summary_max_chars=700, keep_lang_prefix=False),
        LLMSettings(enabled=False),
    )

    assert isinstance(pipeline.summarizer, StubSummarizer)
    assert isinstance(pipeline.translator, StubTranslator)


def test_build_text_pipeline_uses_llm_when_configured() -> None:
    pipeline = build_text_pipeline(
        TextGenerationSettings(
            summary_max_chars=700,
            keep_lang_prefix=False,
            translation_style="concise",
            translation_refine_pass=True,
            translation_glossary={"GPU": "GPU"},
        ),
        LLMSettings(enabled=True, api_key="test-key", model="test-model"),
    )

    assert isinstance(pipeline.summarizer, LLMSummarizer)
    assert isinstance(pipeline.translator, LLMTranslator)
    assert pipeline.translator.style == "concise"
    assert pipeline.translator.refine_pass is True
    assert pipeline.translator.glossary == {"GPU": "GPU"}
