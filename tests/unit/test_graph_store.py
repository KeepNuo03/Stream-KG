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

    await add_entity("entity-redis", "Redis", "doc-1", "chunk-1")
    await add_entity("entity-bert", "BERT", "doc-1", "chunk-1")
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
