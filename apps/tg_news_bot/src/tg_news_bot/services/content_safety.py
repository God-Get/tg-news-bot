"""Content safety checks for moderation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import re

from tg_news_bot.config import ContentSafetySettings


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(slots=True)
class ContentSafetyResult:
    allowed: bool
    reasons: list[str]
    quality_score: float


class ContentSafetyService:
    def __init__(self, settings: ContentSafetySettings) -> None:
        self._settings = settings

    def check(self, *, text: str | None, title: str | None = None) -> ContentSafetyResult:
        if not self._settings.enabled:
            return ContentSafetyResult(allowed=True, reasons=[], quality_score=1.0)

        body = (text or "").strip()
        full_text = f"{(title or '').strip()} {body}".strip()
        full_text_lc = full_text.lower()
        reasons: list[str] = []
        score = 1.0

        if len(body) < self._settings.min_ready_chars:
            reasons.append("too_short")
            score -= 0.6

        links = _URL_RE.findall(full_text)
        if len(links) > self._settings.max_links_in_text:
            reasons.append("too_many_links")
            score -= 0.6

        for keyword in self._settings.ad_keywords:
            candidate = keyword.strip().lower()
            if candidate and candidate in full_text_lc:
                reasons.append(f"ad:{candidate}")
                score -= 0.5
                break

        for keyword in self._settings.toxic_keywords:
            candidate = keyword.strip().lower()
            if candidate and candidate in full_text_lc:
                reasons.append(f"toxic:{candidate}")
                score -= 1.0
                break

        compact = re.sub(r"\s+", " ", body).strip()
        if compact:
            words = [item for item in re.split(r"\W+", compact.lower()) if item]
            unique_ratio = len(set(words)) / float(len(words) or 1)
            if len(words) >= 30 and unique_ratio < 0.35:
                reasons.append("low_variety")
                score -= 0.35
            if _looks_like_shouting(compact):
                reasons.append("shouting")
                score -= 0.2

        has_toxic = any(item.startswith("toxic:") for item in reasons)
        has_ads = any(item.startswith("ad:") for item in reasons)
        allowed = score >= 0.35 and not has_toxic and not has_ads
        return ContentSafetyResult(
            allowed=allowed,
            reasons=reasons,
            quality_score=max(score, -2.0),
        )


def _looks_like_shouting(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 30:
        return False
    upper = sum(1 for ch in letters if ch.isupper())
    return (upper / len(letters)) > 0.65
