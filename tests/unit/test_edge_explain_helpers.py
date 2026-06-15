"""Edge explain route helpers."""

from __future__ import annotations

import networkx as nx

from api.routes.graph import _collect_edge_evidence, _fallback_explanation


def test_collect_edge_evidence_both_directions() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge(
        "a",
        "b",
        key="k1",
        relation_type="proposes",
        evidence="提出该方法",
        evidence_chunk_id="chunk-1",
    )
    chunks, text = _collect_edge_evidence(graph, "b", "a", "proposes")
    assert chunks == ["chunk-1"]
    assert text == "提出该方法"


def test_fallback_explanation_with_evidence() -> None:
    text = _fallback_explanation("A", "B", "提出", "原文依据片段")
    assert "A" in text and "B" in text and "提出" in text and "原文依据片段" in text


def test_fallback_explanation_without_evidence() -> None:
    text = _fallback_explanation("A", "B", "共现", "")
    assert "共现" in text
    assert "引用片段" in text
