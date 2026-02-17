"""Text generation pipeline (LLM-ready)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re
from typing import Protocol

import httpx

from tg_news_bot.config import LLMSettings, TextGenerationSettings
from tg_news_bot.logging import get_logger


_TOPIC_GUIDANCE = {
    "ai": "Focus on model type, benchmarks, datasets, practical impact, and risks.",
    "science": "Focus on method, sample size, key result, and limitations.",
    "space": "Focus on mission goal, organization, payload, timeline, and outcome.",
    "energy": "Focus on technology, efficiency, costs, scale, and deployment stage.",
}


class Summarizer(Protocol):
    async def summarize(
        self,
        text: str,
        *,
        topic_hints: list[str] | None = None,
    ) -> str: ...


class Translator(Protocol):
    async def translate(self, text: str, *, target_lang: str) -> str: ...


class LLMClient(Protocol):
    async def complete(self, *, system_prompt: str, user_prompt: str) -> str: ...


class LLMCircuitOpenError(RuntimeError):
    """Raised when LLM circuit breaker is open."""


@dataclass(slots=True)
class GeneratedPostParts:
    title_ru: str | None
    summary_ru: str


@dataclass(slots=True)
class StubSummarizer:
    max_chars: int = 800

    async def summarize(
        self,
        text: str,
        *,
        topic_hints: list[str] | None = None,  # noqa: ARG002
    ) -> str:
        compact = _compact_text(text)
        if len(compact) <= self.max_chars:
            return compact

        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", compact) if item.strip()]
        if not sentences:
            return _trim_to_limit(compact, self.max_chars)

        chunks: list[str] = []
        total = 0
        for sentence in sentences:
            extra = len(sentence) + (1 if chunks else 0)
            if total + extra > self.max_chars:
                break
            chunks.append(sentence)
            total += extra
        if chunks:
            return " ".join(chunks)
        return _trim_to_limit(compact, self.max_chars)


@dataclass(slots=True)
class StubTranslator:
    keep_lang_prefix: bool = False

    async def translate(self, text: str, *, target_lang: str) -> str:
        compact = _compact_text(text)
        if self.keep_lang_prefix:
            return f"[{target_lang}] {compact}"
        return compact


@dataclass(slots=True)
class OpenAICompatClient:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    temperature: float
    max_retries: int
    retry_backoff_seconds: float
    circuit_breaker_threshold: int
    circuit_breaker_cooldown_seconds: float
    _consecutive_failures: int = field(default=0, init=False, repr=False)
    _opened_until: datetime | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        await self._ensure_circuit_closed()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 2):
            try:
                content = await self._request_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                await self._record_success()
                return content
            except Exception as exc:
                last_error = exc
                await self._record_failure()
                retryable = self._is_retryable(exc)
                if attempt > self.max_retries or not retryable:
                    raise

                wait_seconds = self.retry_backoff_seconds * (2 ** (attempt - 1))
                log = get_logger(__name__)
                log.warning(
                    "llm.retry",
                    attempt=attempt,
                    wait_seconds=wait_seconds,
                    error=type(exc).__name__,
                )
                await asyncio.sleep(wait_seconds)
                await self._ensure_circuit_closed()

        if last_error:
            raise last_error
        raise RuntimeError("LLM completion retry loop failed unexpectedly")

    async def _request_completion(self, *, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("LLM response has no choices")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("LLM response has invalid content")
        return content.strip()

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            return status == 429 or status >= 500
        return False

    async def _ensure_circuit_closed(self) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._opened_until and now < self._opened_until:
                raise LLMCircuitOpenError("LLM circuit breaker is open")
            if self._opened_until and now >= self._opened_until:
                self._opened_until = None
                self._consecutive_failures = 0

    async def _record_success(self) -> None:
        async with self._lock:
            self._consecutive_failures = 0
            self._opened_until = None

    async def _record_failure(self) -> None:
        async with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.circuit_breaker_threshold:
                self._opened_until = datetime.now(timezone.utc) + timedelta(
                    seconds=self.circuit_breaker_cooldown_seconds
                )
                self._consecutive_failures = 0


@dataclass(slots=True)
class LLMSummarizer:
    client: LLMClient
    max_chars: int

    async def summarize(
        self,
        text: str,
        *,
        topic_hints: list[str] | None = None,
    ) -> str:
        compact = _compact_text(text)
        if not compact:
            return ""

        system_prompt = _build_summary_prompt(
            max_chars=self.max_chars,
            topic_hints=topic_hints or [],
        )
        result = await self.client.complete(system_prompt=system_prompt, user_prompt=compact)
        compact_result = _compact_text(result)
        if not compact_result:
            return _trim_to_limit(compact, self.max_chars)
        return _trim_to_limit(compact_result, self.max_chars)


@dataclass(slots=True)
class LLMTranslator:
    client: LLMClient
    keep_lang_prefix: bool = False

    async def translate(self, text: str, *, target_lang: str) -> str:
        compact = _compact_text(text)
        if not compact:
            return ""

        system_prompt = (
            "Translate the text accurately. Keep links, numbers and proper names unchanged when possible. "
            "Return plain text only."
        )
        result = await self.client.complete(
            system_prompt=system_prompt,
            user_prompt=f"Target language: {target_lang}\n\n{compact}",
        )
        translated = _compact_text(result) or compact
        if self.keep_lang_prefix:
            return f"[{target_lang}] {translated}"
        return translated


@dataclass(slots=True)
class TextPipeline:
    summarizer: Summarizer
    translator: Translator

    async def generate_parts(
        self,
        *,
        title_en: str | None,
        text_en: str | None,
        topic_hints: list[str] | None = None,
    ) -> GeneratedPostParts:
        base_text = text_en or title_en or ""
        summary_en = await self.summarizer.summarize(
            base_text,
            topic_hints=topic_hints,
        )
        summary_ru = await self.translator.translate(summary_en, target_lang="RU")
        title_ru: str | None = None
        if title_en:
            title_ru = await self.translator.translate(title_en, target_lang="RU")
        return GeneratedPostParts(title_ru=title_ru, summary_ru=summary_ru)

    async def generate_post(
        self,
        *,
        title_en: str | None,
        text_en: str | None,
        topic_hints: list[str] | None = None,
    ) -> str:
        parts = await self.generate_parts(
            title_en=title_en,
            text_en=text_en,
            topic_hints=topic_hints,
        )
        return compose_post_text(parts.title_ru, parts.summary_ru)


def compose_post_text(title_ru: str | None, summary_ru: str) -> str:
    title = _compact_text(title_ru)
    summary = _compact_text(summary_ru)
    if title:
        if summary:
            return f"{title}\n\n{summary}"
        return title
    return summary


def build_text_pipeline(
    text_settings: TextGenerationSettings,
    llm_settings: LLMSettings,
) -> TextPipeline:
    log = get_logger(__name__)

    if llm_settings.enabled:
        if llm_settings.provider != "openai_compat":
            log.warning(
                "text_pipeline.unsupported_provider",
                provider=llm_settings.provider,
            )
        elif not llm_settings.api_key:
            log.warning("text_pipeline.missing_api_key")
        else:
            client = OpenAICompatClient(
                api_key=llm_settings.api_key,
                base_url=llm_settings.base_url,
                model=llm_settings.model,
                timeout_seconds=llm_settings.timeout_seconds,
                temperature=llm_settings.temperature,
                max_retries=llm_settings.max_retries,
                retry_backoff_seconds=llm_settings.retry_backoff_seconds,
                circuit_breaker_threshold=llm_settings.circuit_breaker_threshold,
                circuit_breaker_cooldown_seconds=llm_settings.circuit_breaker_cooldown_seconds,
            )
            log.info(
                "text_pipeline.llm_enabled",
                provider=llm_settings.provider,
                model=llm_settings.model,
            )
            return TextPipeline(
                summarizer=LLMSummarizer(
                    client=client,
                    max_chars=text_settings.summary_max_chars,
                ),
                translator=LLMTranslator(
                    client=client,
                    keep_lang_prefix=text_settings.keep_lang_prefix,
                ),
            )

    log.info("text_pipeline.stub_enabled")
    return TextPipeline(
        summarizer=StubSummarizer(max_chars=text_settings.summary_max_chars),
        translator=StubTranslator(keep_lang_prefix=text_settings.keep_lang_prefix),
    )


def _build_summary_prompt(*, max_chars: int, topic_hints: list[str]) -> str:
    normalized_topics = [item for item in (_normalize_topic(topic) for topic in topic_hints) if item]
    unique_topics: list[str] = []
    for topic in normalized_topics:
        if topic not in unique_topics:
            unique_topics.append(topic)

    instructions = [
        "Summarize the article in English.",
        f"Keep only key facts and stay within {max_chars} characters.",
        "Do not include hype or marketing language.",
        "Return plain text only.",
    ]

    for topic in unique_topics:
        guidance = _TOPIC_GUIDANCE.get(topic)
        if guidance:
            instructions.append(guidance)

    if unique_topics:
        instructions.append(f"Topic hints: {', '.join(unique_topics)}.")

    return " ".join(instructions)


def _normalize_topic(value: str) -> str:
    text = _compact_text(value).lower()
    if not text:
        return ""
    aliases = {
        "new energy": "energy",
        "renewable": "energy",
        "renewables": "energy",
        "spaceflight": "space",
        "artificial intelligence": "ai",
    }
    return aliases.get(text, text)


def _compact_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _trim_to_limit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."
