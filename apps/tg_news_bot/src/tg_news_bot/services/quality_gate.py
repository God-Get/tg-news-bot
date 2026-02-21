"""Quality gate before moving draft to READY."""

from __future__ import annotations

from dataclasses import dataclass
import re

from tg_news_bot.config import QualityGateSettings
from tg_news_bot.services.source_text import sanitize_source_text


_PAYWALL_PATTERNS = [
    re.compile(r"(?is)\bthank\s+you\s+for\s+visiting\b.*?\bnature\.com\b"),
    re.compile(r"(?is)\byou\s+are\s+using\s+a\s+browser\s+version\s+with\s+limited\s+support\s+for\s+css\b"),
    re.compile(r"(?is)\baccess\s+options\b"),
    re.compile(r"(?is)\bsubscribe\s+to\s+this\s+journal\b"),
    re.compile(r"(?is)\brent\s+or\s+buy\s+this\s+article\b"),
    re.compile(r"(?is)\bprices\s+may\s+be\s+subject\s+to\s+local\s+taxes\b"),
]

_NOISE_LINE_PATTERNS = [
    re.compile(r"(?i)^\s*(date|source|summary|share)\s*:\s*"),
    re.compile(r"(?i)^\s*(references?|access options)\b"),
    re.compile(r"(?i)^\s*doi\s*:"),
    re.compile(r"(?i)\bsubscribe\b.*\bpodcast\b"),
]


@dataclass(slots=True)
class QualityGateResult:
    status: str
    text: str
    reasons: list[str]

    @property
    def should_archive(self) -> bool:
        return self.status == "reject"

    @property
    def fallback_applied(self) -> bool:
        return self.status == "fallback"


class QualityGateService:
    def __init__(self, settings: QualityGateSettings) -> None:
        self._settings = settings

    def evaluate(
        self,
        *,
        current_text: str | None,
        title: str | None,
        source_text: str | None,
    ) -> QualityGateResult:
        if not self._settings.enabled:
            return QualityGateResult(
                status="ok",
                text=(current_text or "").strip(),
                reasons=[],
            )

        cleaned_current, marker_hits = self._cleanup_text(current_text)
        reasons: list[str] = []
        if marker_hits:
            reasons.append("paywall_noise")

        if self._is_usable(cleaned_current):
            if cleaned_current != (current_text or "").strip():
                reasons.append("service_noise_removed")
            return QualityGateResult(status="ok", text=cleaned_current, reasons=reasons)

        reasons.append("too_short_or_empty")
        fallback_candidate = self._build_fallback_text(
            title=title,
            source_text=source_text,
        )
        cleaned_fallback, _ = self._cleanup_text(fallback_candidate)
        if self._is_usable(cleaned_fallback):
            reasons.append("fallback_from_source")
            return QualityGateResult(status="fallback", text=cleaned_fallback, reasons=reasons)

        reasons.append("auto_archive")
        return QualityGateResult(status="reject", text=cleaned_current, reasons=reasons)

    def _build_fallback_text(self, *, title: str | None, source_text: str | None) -> str:
        source_clean = sanitize_source_text(source_text)
        source_clean = re.sub(r"\s+", " ", source_clean).strip()
        snippet = source_clean[: self._settings.fallback_snippet_chars].strip()
        snippet = snippet.rstrip(".,;:") + "."
        title_clean = re.sub(r"\s+", " ", (title or "").strip())

        if title_clean and snippet:
            return f"{title_clean}\n\n{snippet}"
        if snippet:
            return snippet
        return title_clean

    def _cleanup_text(self, raw_text: str | None) -> tuple[str, int]:
        text = sanitize_source_text(raw_text)
        marker_hits = 0
        for pattern in _PAYWALL_PATTERNS:
            marker_hits += len(pattern.findall(text))
            text = pattern.sub(" ", text)

        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(pattern.search(line) for pattern in _NOISE_LINE_PATTERNS):
                continue
            if line.lower().startswith("http://") or line.lower().startswith("https://"):
                continue
            lines.append(line)
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
        return cleaned, marker_hits

    def _is_usable(self, text: str) -> bool:
        if not text:
            return False
        words = re.findall(r"[0-9A-Za-zА-Яа-яЁё_]+", text)
        if len(words) < self._settings.min_words:
            return False
        meaningful_chars = sum(1 for ch in text if ch.isalnum())
        if meaningful_chars < self._settings.min_meaningful_chars:
            return False
        return True
