"""P2 smoke: 双写存储与图谱级联删除一致性。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.graph_update import GraphUpdateService
from stream_kg.kg.kg_cleanup import KgCleanupService
from stream_kg.kg.models import ChunkRecord, EntityMention, ResolveResult, TemporalEdge, new_edge_id
from stream_kg.storage.sqlite_store import SQLiteStore


class _NoopQdrant:
    def upsert_entities(self, *, entities, vectors) -> None:  # noqa: ANN001
        return None

    def delete_entity(self, entity_id: str) -> None:
        return None

    def delete_chunks_by_doc(self, doc_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_dual_write_and_delete_cascade(tmp_path: Path) -> None:
    db_path = tmp_path / "meta.db"
    graph_path = tmp_path / "graph.pkl"
    sqlite = SQLiteStore(str(db_path))
    await sqlite.initialize()

    doc_id = "doc-smoke-1"
    await sqlite.create_document(doc_id=doc_id, title="Smoke", doc_type="web", source_uri="https://example.com")
    chunk = ChunkRecord(
        chunk_id="chunk-smoke-1",
        doc_id=doc_id,
        content="Redis improves Qdrant retrieval latency.",
        chunk_type="text",
        char_start=0,
        char_end=39,
        token_count=6,
    )
    await sqlite.upsert_chunks([chunk])

    graph_store = GraphStore(str(graph_path))
    graph_update = GraphUpdateService(
        graph_store=graph_store,
        qdrant_store=_NoopQdrant(),  # type: ignore[arg-type]
        sqlite_store=sqlite,
    )
    await graph_store.initialize()

    redis_mention = EntityMention(
        mention_id="mention-1",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form="Redis",
        entity_type="method",
        char_start=0,
        char_end=5,
        context_snippet=chunk.content,
        vector=[0.1, 0.2],
    )
    qdrant_mention = EntityMention(
        mention_id="mention-2",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form="Qdrant",
        entity_type="method",
        char_start=14,
        char_end=20,
        context_snippet=chunk.content,
        vector=[0.2, 0.3],
    )
    await graph_update.apply_resolution(
        mention=redis_mention,
        result=ResolveResult(
            action="create",
            entity_id="entity-redis",
            canonical_name="Redis",
            entity_type="method",
            score=0.0,
        ),
    )
    await graph_update.apply_resolution(
        mention=qdrant_mention,
        result=ResolveResult(
            action="create",
            entity_id="entity-qdrant",
            canonical_name="Qdrant",
            entity_type="method",
            score=0.0,
        ),
    )
    await graph_update.add_edges(
        [
            TemporalEdge(
                edge_id=new_edge_id(),
                head_entity_id="entity-redis",
                tail_entity_id="entity-qdrant",
                relation_type="improves",
                confidence=0.82,
                evidence_chunk_id=chunk.chunk_id,
                created_at=datetime.now(UTC),
            )
        ]
    )
    await graph_store.persist()

    stats = await sqlite.stats()
    assert stats["entities"] >= 1
    assert stats["edges"] >= 1

    cleanup = KgCleanupService(sqlite_store=sqlite, graph_store=graph_store, qdrant_store=_NoopQdrant())  # type: ignore[arg-type]
    await cleanup.cleanup_document(doc_id)

    stats_after = await sqlite.stats()
    assert stats_after["documents"] == 0
    assert stats_after["chunks"] == 0
    assert stats_after["entities"] == 0
    assert stats_after["edges"] == 0

    exported = await graph_store.export_graph()
    assert exported["stats"]["node_count"] == 0
