"""Unit tests for ego subgraph export."""

from __future__ import annotations

import pytest

from stream_kg.kg.graph_store import GraphStore


@pytest.fixture
async def store(tmp_path) -> GraphStore:
    s = GraphStore(str(tmp_path / "graph.pkl"))
    await s.initialize()
    return s


async def test_export_ego_subgraph_from_doc(store: GraphStore) -> None:
    await store.upsert_document_node(doc_id="d1", doc_type="pdf", title="Paper A")
    await store.upsert_entity_node(
        entity_id="e1",
        canonical_name="Transformer",
        entity_type="method",
        doc_id="d1",
        chunk_id="c1",
        salience=0.9,
    )
    await store.upsert_doc_entity_link(doc_id="d1", entity_id="e1", salience=0.9)
    await store.upsert_entity_edge_v2(
        head_entity_id="e1",
        tail_entity_id="e2",
        relation_type="uses",
        confidence=0.8,
        evidence="uses attention",
        evidence_chunk_id="c1",
    )
    await store.upsert_entity_node(
        entity_id="e2",
        canonical_name="Attention",
        entity_type="concept",
        doc_id="d1",
        chunk_id="c1",
        salience=0.7,
    )

    result = await store.export_graph(focus_doc_id="d1", hop=1, view_mode="mixed")
    assert result["stats"]["node_count"] >= 2
    assert any(n["id"] == "e1" for n in result["nodes"])
