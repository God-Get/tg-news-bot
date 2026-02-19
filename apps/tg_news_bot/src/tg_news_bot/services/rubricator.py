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
    _TOPIC_RU_HASHTAGS = {
        "ai": "ии",
        "science": "наука",
        "space": "космос",
        "energy": "энергия",
        "biotech": "биотех",
    }
    _HASHTAG_RU_ALIASES = {
        "ai": "ии",
        "artificial_intelligence": "ии",
        "machine_learning": "машинное_обучение",
        "deep_learning": "глубокое_обучение",
        "neural_network": "нейросети",
        "science": "наука",
        "space": "космос",
        "space_tech": "космос",
        "energy": "энергия",
        "new_energy": "новая_энергия",
        "biotech": "биотех",
        "technology": "технологии",
        "tech": "технологии",
    }
    _HASHTAG_CANONICAL = {
        "artificial_intelligence": "ai",
        "machine_learning": "machine_learning",
        "deep_learning": "deep_learning",
        "neural_network": "neural_network",
        "space_tech": "space",
        "new_energy": "energy",
        "technology": "technology",
        "tech": "technology",
    }
    _TOPIC_PRIORITY = {
        "ai": 1,
        "science": 2,
        "space": 3,
        "energy": 4,
        "biotech": 5,
    }
    _HASHTAG_STOPWORDS = {
        "update",
        "updates",
        "article",
        "articles",
        "summary",
        "source",
        "report",
        "official",
        "today",
        "yesterday",
    }

    def classify(
        self,
        *,
        title: str | None,
        text: str | None,
        trend_keywords: list[str] | None = None,
        limit: int = 6,
        hashtag_mode: str = "both",
    ) -> RubricationResult:
        mode = self._normalize_hashtag_mode(hashtag_mode)
        content = f"{title or ''} {text or ''}".strip().lower()
        topic_hits: dict[str, int] = {}
        for topic, markers in self._TOPIC_RULES.items():
            hits = sum(1 for marker in markers if marker in content)
            if hits > 0:
                topic_hits[topic] = hits
        topics = sorted(
            topic_hits.keys(),
            key=lambda item: (-topic_hits[item], self._TOPIC_PRIORITY.get(item, 999), item),
        )

        hashtags: list[str] = []
        seen_canonical: set[str] = set()

        def append_tag(raw_token: str) -> None:
            token = self._normalize_hashtag_token(raw_token)
            if not token or not self._is_quality_tag(token):
                return
            canonical = self._canonical_tag(token)
            if canonical in seen_canonical:
                return
            seen_canonical.add(canonical)
            hashtags.append(f"#{token}")

        for topic in topics:
            for tag in self._iter_hashtag_variants(topic, mode=mode, topic_hint=True):
                append_tag(tag)
                if len(hashtags) >= limit:
                    break
            if len(hashtags) >= limit:
                break

        if trend_keywords:
            for keyword in trend_keywords:
                for tag in self._iter_hashtag_variants(keyword, mode=mode):
                    append_tag(tag)
                    if len(hashtags) >= limit:
                        break
                if len(hashtags) >= limit:
                    break

        if not hashtags:
            for fallback in ("science", "tech"):
                for tag in self._iter_hashtag_variants(fallback, mode=mode, topic_hint=True):
                    append_tag(tag)
                    if len(hashtags) >= min(limit, 2):
                        break
                if len(hashtags) >= min(limit, 2):
                    break

        return RubricationResult(topics=topics[:3], hashtags=hashtags[:limit])

    @classmethod
    def _iter_hashtag_variants(
        cls,
        value: str,
        *,
        mode: str,
        topic_hint: bool = False,
    ) -> list[str]:
        base = cls._normalize_hashtag_token(value)
        if not base:
            return []

        translated_token = ""
        translated = cls._TOPIC_RU_HASHTAGS.get(base) if topic_hint else None
        if not translated:
            translated = cls._HASHTAG_RU_ALIASES.get(base)
        if translated:
            translated_token = cls._normalize_hashtag_token(translated)

        if mode == "en":
            return [base]
        if mode == "ru":
            if translated_token:
                return [translated_token]
            if cls._contains_cyrillic(base):
                return [base]
            return []

        variants = [base]
        if translated_token and translated_token not in variants:
            variants.append(translated_token)
        return variants

    @staticmethod
    def _normalize_hashtag_mode(value: str) -> str:
        mode = (value or "both").strip().lower()
        if mode not in {"ru", "en", "both"}:
            return "both"
        return mode

    @staticmethod
    def _normalize_hashtag_token(value: str) -> str:
        clean = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ_]+", "_", value.strip().lower()).strip("_")
        if not clean:
            return ""
        if clean[0].isdigit():
            clean = f"tag_{clean}"
        return clean

    @classmethod
    def _canonical_tag(cls, token: str) -> str:
        return cls._HASHTAG_CANONICAL.get(token, token)

    @classmethod
    def _is_quality_tag(cls, token: str) -> bool:
        if token in cls._HASHTAG_STOPWORDS:
            return False
        if len(token) < 2 or len(token) > 32:
            return False
        if token.isdigit():
            return False
        if token.startswith("http"):
            return False
        letters = [ch for ch in token if ch != "_" and ch.isalpha()]
        if len(letters) < 2:
            return False
        return True

    @staticmethod
    def _contains_cyrillic(value: str) -> bool:
        return bool(re.search(r"[а-яё]", value.lower()))
