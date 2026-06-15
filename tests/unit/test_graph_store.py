"""Unit tests for NetworkX graph store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.models import EntityMention, TemporalEdge, new_edge_id, new_mention_id


@pytest.mark.asyncio
async def test_remove_document_prunes_nodes_and_edges(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.pkl"
    store = GraphStore(str(graph_path))
    await store.initialize()

    mention = EntityMention(
        mention_id=new_mention_id(),
        doc_id="doc-a",
        chunk_id="chunk-a",
        surface_form="Redis",
        entity_type="method",
        char_start=0,
        char_end=5,
        context_snippet="Redis cache",
    )
    await store.upsert_entity_from_mention(
        entity_id="entity-a",
        canonical_name="Redis",
        entity_type="method",
        mention=mention,
    )
    await store.add_temporal_edge(
        TemporalEdge(
            edge_id=new_edge_id(),
            head_entity_id="entity-a",
            tail_entity_id="entity-b",
            relation_type="mentions",
            confidence=0.7,
            evidence_chunk_id="chunk-a",
            created_at=datetime.now(UTC),
        )
    )
    await store.upsert_entity_from_mention(
        entity_id="entity-b",
        canonical_name="Qdrant",
        entity_type="method",
        mention=EntityMention(
            mention_id=new_mention_id(),
            doc_id="doc-b",
            chunk_id="chunk-b",
            surface_form="Qdrant",
            entity_type="method",
            char_start=0,
            char_end=6,
            context_snippet="Qdrant vector db",
        ),
    )

    removed = await store.remove_document("doc-a", chunk_ids=["chunk-a"])
    await store.persist()

    assert "entity-a" in removed
    exported = await store.export_graph(min_mentions=1, relation_type="all")
    node_ids = {node["id"] for node in exported["nodes"]}
    assert "entity-a" not in node_ids
    # 连通子图导出：无边孤立节点不出现在画布数据中
    assert exported["stats"]["node_count"] == 0
    assert exported["stats"]["edge_count"] == 0
    remaining_ids = await store.entity_ids()
    assert "entity-b" in remaining_ids


@pytest.mark.asyncio
async def test_export_connected_subgraph_skips_isolated_nodes(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.pkl"
    store = GraphStore(str(graph_path))
    await store.initialize()

    async def add_entity(entity_id: str, name: str, doc_id: str, chunk_id: str) -> None:
        await store.upsert_entity_from_mention(
            entity_id=entity_id,
            canonical_name=name,
            entity_type="method",
            mention=EntityMention(
                mention_id=new_mention_id(),
                doc_id=doc_id,
                chunk_id=chunk_id,
                surface_form=name,
                entity_type="method",
                char_start=0,
                char_end=len(name),
                context_snippet=name,
            ),
        )

    await add_entity("entity-redis", "Transformer", "doc-1", "chunk-1")
    await add_entity("entity-bert", "Attention", "doc-1", "chunk-1")
    await add_entity("entity-alone", "Kubernetes", "doc-1", "chunk-2")

    await store.add_temporal_edge(
        TemporalEdge(
            edge_id=new_edge_id(),
            head_entity_id="entity-redis",
            tail_entity_id="entity-bert",
            relation_type="mentions",
            confidence=0.72,
            evidence_chunk_id="chunk-1",
            created_at=datetime.now(UTC),
        )
    )
    await store.persist()

    exported = await store.export_graph(min_mentions=1, relation_type="balanced", limit_nodes=36)
    node_ids = {node["id"] for node in exported["nodes"]}
    assert "entity-redis" in node_ids
    assert "entity-bert" in node_ids
    assert "entity-alone" not in node_ids
    assert exported["stats"]["edge_count"] >= 1


@pytest.mark.asyncio
async def test_export_view_modes_l0_l1_mixed(tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.pkl"
    store = GraphStore(str(graph_path))
    await store.initialize()
    await store.upsert_document_node(doc_id="d1", doc_type="pdf", title="Doc One")
    await store.upsert_document_node(doc_id="d2", doc_type="pdf", title="Doc Two")
    await store.upsert_entity_node(
        entity_id="e1",
        canonical_name="Transformer",
        entity_type="method",
        doc_id="d1",
        chunk_id="c1",
        salience=0.8,
        parent_doc_id="d1",
    )
    await store.upsert_entity_node(
        entity_id="e2",
        canonical_name="Attention",
        entity_type="method",
        doc_id="d2",
        chunk_id="c2",
        salience=0.7,
        parent_doc_id="d2",
    )
    await store.upsert_doc_entity_link(doc_id="d1", entity_id="e1", salience=0.8)
    await store.upsert_doc_entity_link(doc_id="d2", entity_id="e2", salience=0.7)
    await store.upsert_entity_edge_v2(
        head_entity_id="e1",
        tail_entity_id="e2",
        relation_type="improves",
        confidence=0.82,
        evidence_chunk_id="c1",
    )
    # cross-doc
    await store.build_cross_doc_edges(
        doc_entity_links=[
            {"doc_id": "d1", "entity_id": "e_shared_1", "salience_max": 0.7},
            {"doc_id": "d1", "entity_id": "e_shared_2", "salience_max": 0.8},
            {"doc_id": "d1", "entity_id": "e_shared_3", "salience_max": 0.9},
            {"doc_id": "d2", "entity_id": "e_shared_1", "salience_max": 0.7},
            {"doc_id": "d2", "entity_id": "e_shared_2", "salience_max": 0.8},
            {"doc_id": "d2", "entity_id": "e_shared_3", "salience_max": 0.9},
        ]
    )
    await store.persist()

    l1 = await store.export_graph(view_mode="l1", min_mentions=1, relation_type="all")
    assert l1["stats"]["view_mode"] == "l1"
    assert all(node["layer"] == "L1" for node in l1["nodes"])
    assert any(edge["relation_type"] == "improves" for edge in l1["edges"])

    l0 = await store.export_graph(view_mode="l0", min_mentions=1, relation_type="all")
    assert l0["stats"]["view_mode"] == "l0"
    assert all(node["layer"] == "L0" for node in l0["nodes"])
    assert any(edge["relation_type"] == "shares_entity" for edge in l0["edges"])

    mixed = await store.export_graph(view_mode="mixed", min_mentions=1, relation_type="all")
    assert mixed["stats"]["view_mode"] == "mixed"
    layers = {node["layer"] for node in mixed["nodes"]}
    assert layers == {"L0", "L1"}
    rels = {edge["relation_type"] for edge in mixed["edges"]}
    assert "mentions" in rels
    assert "improves" in rels
