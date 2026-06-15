"""Unit tests for change_diff."""

from __future__ import annotations

import networkx as nx

from stream_kg.kg.change_diff import diff_edge_snapshots, snapshot_graph_edges


def test_snapshot_and_diff_new_edges() -> None:
    g1 = nx.MultiDiGraph()
    g1.add_edge("a", "b", key="extends", relation_type="extends")
    g2 = nx.MultiDiGraph()
    g2.add_edge("a", "b", key="extends", relation_type="extends")
    g2.add_edge("b", "c", key="improves", relation_type="improves")

    before = snapshot_graph_edges(g1)
    after = snapshot_graph_edges(g2)
    added = diff_edge_snapshots(before, after)
    assert len(added) == 1
    assert added[0]["relation_type"] == "improves"
