"""Topic classification and hashtag generation."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(slots=True)
class RubricationResult:
    topics: list[str]
    hashtags: list[str]


class RubricatorService:
    _TOPIC_RULES = {
        "ai": [
            "ai",
            "artificial intelligence",
            "llm",
            "gpt",
            "transformer",
            "inference",
            "deep learning",
            "neural network",
        ],
        "science": [
            "research",
            "study",
            "scientists",
            "experiment",
            "peer reviewed",
            "journal",
            "clinical",
        ],
        "space": [
            "space",
            "nasa",
            "spacex",
            "orbit",
            "satellite",
            "launch",
            "astronomy",
            "lunar",
            "mars",
        ],
        "energy": [
            "battery",
            "solar",
            "wind",
            "fusion",
            "hydrogen",
            "renewable",
            "grid",
            "power plant",
        ],
        "biotech": [
            "biotech",
            "genome",
            "crispr",
            "drug discovery",
            "protein",
            "cell therapy",
        ],
    }

    def classify(
        self,
        *,
        title: str | None,
        text: str | None,
        trend_keywords: list[str] | None = None,
        limit: int = 6,
    ) -> RubricationResult:
        content = f"{title or ''} {text or ''}".strip().lower()
        topics: list[str] = []
        for topic, markers in self._TOPIC_RULES.items():
            if any(marker in content for marker in markers):
                topics.append(topic)

        hashtags: list[str] = []
        seen: set[str] = set()
        for topic in topics:
            tag = self._to_hashtag(topic)
            if tag and tag not in seen:
                seen.add(tag)
                hashtags.append(tag)

        if trend_keywords:
            for keyword in trend_keywords:
                tag = self._to_hashtag(keyword)
                if tag and tag not in seen:
                    seen.add(tag)
                    hashtags.append(tag)
                if len(hashtags) >= limit:
                    break

        if not hashtags:
            for fallback in ("science", "tech"):
                tag = self._to_hashtag(fallback)
                if tag and tag not in seen:
                    hashtags.append(tag)
                    seen.add(tag)
                if len(hashtags) >= min(limit, 2):
                    break

        return RubricationResult(topics=topics[:3], hashtags=hashtags[:limit])

    @staticmethod
    def _to_hashtag(value: str) -> str:
        clean = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip().lower()).strip("_")
        if not clean:
            return ""
        if clean[0].isdigit():
            clean = f"tag_{clean}"
        return f"#{clean}"
