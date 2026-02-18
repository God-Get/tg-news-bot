"""Scoring service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tg_news_bot.config import ScoringSettings


@dataclass(slots=True)
class ScoreResult:
    score: float
    reasons: dict


class ScoringService:
    def __init__(self, settings: ScoringSettings) -> None:
        self._settings = settings

    def score(
        self,
        *,
        text: str | None,
        title: str | None,
        domain: str | None,
        published_at: datetime | None,
        trend_boosts: dict[str, float] | None = None,
        source_trust_score: float | None = None,
    ) -> ScoreResult:
        score = 0.0
        reasons: dict[str, float | str] = {}

        if not text:
            reasons["no_text"] = -2.0
            return ScoreResult(score=-2.0, reasons=reasons)

        length = len(text)
        reasons["length"] = float(length)
        if length < self._settings.min_length_chars:
            ratio = length / float(self._settings.min_length_chars)
            if ratio < 0.4:
                penalty = -1.0
            elif ratio < 0.75:
                penalty = -0.5
            else:
                penalty = -0.2
            score += penalty
            reasons["length_penalty"] = penalty
        elif length > self._settings.max_length_chars:
            score -= 0.5
            reasons["length_penalty"] = -0.5
        else:
            score += 1.0
            reasons["length_bonus"] = 1.0

        if published_at:
            now = datetime.now(timezone.utc)
            hours = (now - published_at).total_seconds() / 3600
            reasons["age_hours"] = hours
            if hours <= self._settings.freshness_hours:
                score += 1.0
                reasons["fresh_bonus"] = 1.0
            else:
                score -= 0.5
                reasons["fresh_penalty"] = -0.5

        text_lower = text.lower()
        title_lower = ""
        if title:
            title_lower = title.lower()
            text_lower = f"{title_lower} {text_lower}"

        for keyword, boost in self._settings.keyword_boosts.items():
            keyword_lc = keyword.lower()
            if keyword_lc in text_lower:
                applied_boost = float(boost)
                if title_lower and keyword_lc in title_lower:
                    applied_boost *= self._settings.title_keyword_multiplier
                    reasons[f"kw_title:{keyword}"] = applied_boost
                reasons[f"kw:{keyword}"] = applied_boost
                score += applied_boost

        if domain:
            for dom, boost in self._settings.domain_boosts.items():
                if domain.endswith(dom):
                    score += boost
                    reasons[f"domain:{dom}"] = boost

        if trend_boosts:
            trend_applied = 0
            for keyword, boost in trend_boosts.items():
                keyword_lc = keyword.lower().strip()
                if not keyword_lc:
                    continue
                if keyword_lc in text_lower:
                    applied = float(boost)
                    score += applied
                    reasons[f"trend:{keyword_lc}"] = applied
                    trend_applied += 1
                if trend_applied >= 6:
                    break

        if source_trust_score is not None:
            trust = max(min(float(source_trust_score), 10.0), -10.0)
            trust_boost = trust * 0.15
            score += trust_boost
            reasons["source_trust"] = trust_boost

        return ScoreResult(score=score, reasons=reasons)
