"""Metrics registry and Prometheus rendering."""

from __future__ import annotations

from threading import Lock

LabelKey = tuple[tuple[str, str], ...]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, LabelKey], float] = {}
        self._gauges: dict[tuple[str, LabelKey], float] = {}
        self._types: dict[str, str] = {}

    def inc_counter(
        self, name: str, value: float = 1.0, *, labels: dict[str, str] | None = None
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._types[name] = "counter"
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(
        self, name: str, value: float, *, labels: dict[str, str] | None = None
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._types[name] = "gauge"
            self._gauges[key] = float(value)

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            types = dict(self._types)

        lines: list[str] = []

        for name, entries in self._group_items(counters).items():
            if types.get(name) == "counter":
                lines.append(f"# TYPE {name} counter")
            for labels, value in entries:
                lines.append(f"{name}{_format_labels(labels)} {value}")

        for name, entries in self._group_items(gauges).items():
            if types.get(name) == "gauge":
                lines.append(f"# TYPE {name} gauge")
            for labels, value in entries:
                lines.append(f"{name}{_format_labels(labels)} {value}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> tuple[str, LabelKey]:
        if not labels:
            return name, tuple()
        items = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        return name, items

    @staticmethod
    def _group_items(
        data: dict[tuple[str, LabelKey], float]
    ) -> dict[str, list[tuple[LabelKey, float]]]:
        grouped: dict[str, list[tuple[LabelKey, float]]] = {}
        for (name, labels), value in data.items():
            grouped.setdefault(name, []).append((labels, value))
        for entries in grouped.values():
            entries.sort(key=lambda item: item[0])
        return dict(sorted(grouped.items()))


def _format_labels(labels: LabelKey) -> str:
    if not labels:
        return ""
    parts = []
    for key, value in labels:
        safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f"{key}=\"{safe_value}\"")
    return "{" + ",".join(parts) + "}"


metrics = MetricsRegistry()
