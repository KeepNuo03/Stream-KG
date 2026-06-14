"""Query routing: vector vs hybrid (Phase 3)."""

from __future__ import annotations

from stream_kg.config import settings
from stream_kg.kg.models import RetrievalMode

GRAPH_KEYWORDS = [
    "对比",
    "区别",
    "演变",
    "关系",
    "改进",
    "矛盾",
    "冲突",
    "compare",
    "difference",
    "evolution",
    "relation",
    "improve",
    "conflict",
]


def route_query(query: str) -> RetrievalMode:
    """Route query by intent and feature flags."""
    if not settings.feature_graph_router_enabled:
        return "vector"

    normalized = query.lower()
    if any(keyword in normalized for keyword in GRAPH_KEYWORDS):
        return "hybrid"
    return "vector"
