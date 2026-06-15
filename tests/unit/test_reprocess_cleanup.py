"""Phase-X 修复单测：

1. KgCleanupService.cleanup_for_reprocess 清空 chunks/mentions/edges/doc_entity_links
   并保留 documents 行 + 复位 kg_status。
2. GraphUpdateService.flush_entity_vectors 把批量 entries 一次性 PUT 给 Qdrant，
   而不是每条都触发一次（10 分钟 -> 数秒的关键路径）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.graph_update import GraphUpdateService
from stream_kg.kg.kg_cleanup import KgCleanupService
from stream_kg.kg.models import ChunkRecord, EntityMention, ResolveResult, TemporalEdge, new_edge_id
from stream_kg.storage.sqlite_store import SQLiteStore


class _RecordingQdrant:
    """记录所有 Qdrant 调用，用于断言批量 vs 单条。"""

    def __init__(self) -> None:
        self.upsert_calls: list[tuple[list[dict[str, Any]], list[list[float]]]] = []
        self.deleted_entities: list[str] = []
        self.deleted_chunks_by_doc: list[str] = []

    def upsert_entities(self, entities, vectors) -> None:  # noqa: ANN001
        self.upsert_calls.append((list(entities), list(vectors)))

    def delete_entity(self, entity_id: str) -> None:
        self.deleted_entities.append(entity_id)

    def delete_chunks_by_doc(self, doc_id: str) -> None:
        self.deleted_chunks_by_doc.append(doc_id)


async def _seed_doc(sqlite: SQLiteStore, graph_store: GraphStore, doc_id: str) -> str:
    """造一份带 1 chunk + 2 entities + 1 edge 的文档数据。"""
    await sqlite.create_document(
        doc_id=doc_id, title="repro", doc_type="web", source_uri="https://x.test"
    )
    chunk = ChunkRecord(
        chunk_id=f"{doc_id}-chunk-1",
        doc_id=doc_id,
        content="Transformer extends Attention significantly.",
        chunk_type="text",
        char_start=0,
        char_end=43,
        token_count=5,
    )
    await sqlite.upsert_chunks([chunk])

    qdrant = _RecordingQdrant()
    graph_update = GraphUpdateService(
        graph_store=graph_store,
        qdrant_store=qdrant,  # type: ignore[arg-type]
        sqlite_store=sqlite,
    )
    await graph_store.initialize()

    m1 = EntityMention(
        mention_id=f"{doc_id}-m1",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form="Transformer",
        entity_type="method",
        char_start=0,
        char_end=11,
        context_snippet=chunk.content,
        vector=[0.1, 0.2],
    )
    m2 = EntityMention(
        mention_id=f"{doc_id}-m2",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form="Attention",
        entity_type="method",
        char_start=20,
        char_end=29,
        context_snippet=chunk.content,
        vector=[0.3, 0.4],
    )
    await graph_update.apply_resolution(
        mention=m1,
        result=ResolveResult(action="create", entity_id="e-trans", canonical_name="Transformer", entity_type="method", score=0.0),
    )
    await graph_update.apply_resolution(
        mention=m2,
        result=ResolveResult(action="create", entity_id="e-attn", canonical_name="Attention", entity_type="method", score=0.0),
    )
    await graph_update.add_edges(
        [
            TemporalEdge(
                edge_id=new_edge_id(),
                head_entity_id="e-trans",
                tail_entity_id="e-attn",
                relation_type="extends",
                confidence=0.78,
                evidence_chunk_id=chunk.chunk_id,
                created_at=datetime.now(UTC),
            )
        ]
    )
    await sqlite.upsert_doc_entity_link(
        doc_id=doc_id, entity_id="e-trans", mention_count_delta=1,
        first_chunk_id=chunk.chunk_id, salience=0.9,
    )
    await sqlite.upsert_doc_entity_link(
        doc_id=doc_id, entity_id="e-attn", mention_count_delta=1,
        first_chunk_id=chunk.chunk_id, salience=0.9,
    )
    await sqlite.insert_kg_extraction_log(
        log_id=f"{doc_id}-log-1",
        chunk_id=chunk.chunk_id,
        doc_id=doc_id,
        status="ok",
        attempt_count=1,
        elapsed_sec=0.1,
        prompt_tokens=10,
        completion_tokens=20,
        cost_yuan=0.001,
        entities_count=2,
        relations_count=1,
        error_message=None,
        raw_output=None,
    )
    await graph_store.persist()
    return chunk.chunk_id


@pytest.mark.asyncio
async def test_cleanup_for_reprocess_clears_kg_data_but_keeps_document(tmp_path: Path) -> None:
    sqlite = SQLiteStore(str(tmp_path / "meta.db"))
    await sqlite.initialize()
    graph_store = GraphStore(str(tmp_path / "graph.pkl"))
    doc_id = "doc-reprocess"

    await _seed_doc(sqlite, graph_store, doc_id)

    stats_before = await sqlite.stats()
    assert stats_before["chunks"] == 1
    assert stats_before["entities"] >= 2
    assert stats_before["edges"] == 1

    qdrant = _RecordingQdrant()
    cleanup = KgCleanupService(
        sqlite_store=sqlite, graph_store=graph_store, qdrant_store=qdrant,  # type: ignore[arg-type]
    )
    await cleanup.cleanup_for_reprocess(doc_id)

    # 文档行保留，KG 数据清空，状态复位
    doc = await sqlite.get_document(doc_id)
    assert doc is not None
    assert doc.kg_status == "unprocessed"
    assert doc.kg_error_message is None

    stats_after = await sqlite.stats()
    assert stats_after["documents"] == 1
    assert stats_after["chunks"] == 0
    assert stats_after["edges"] == 0
    # entities 应被孤立清理（这里没有其他 doc 持有它们）
    assert stats_after["entities"] == 0

    links = await sqlite.list_doc_entity_links(doc_id=doc_id)
    assert links == []
    logs = await sqlite.get_kg_extraction_stats(doc_id=doc_id)
    assert logs["total"] == 0

    # Qdrant 端应做过 chunk 清理
    assert qdrant.deleted_chunks_by_doc == [doc_id]


@pytest.mark.asyncio
async def test_flush_entity_vectors_is_single_batched_call(tmp_path: Path) -> None:
    sqlite = SQLiteStore(str(tmp_path / "meta.db"))
    await sqlite.initialize()
    graph_store = GraphStore(str(tmp_path / "graph.pkl"))
    await graph_store.initialize()
    qdrant = _RecordingQdrant()
    service = GraphUpdateService(
        graph_store=graph_store, qdrant_store=qdrant, sqlite_store=sqlite,  # type: ignore[arg-type]
    )

    entries = [
        {"entity_id": f"e{i}", "canonical_name": f"E{i}", "entity_type": "method", "aliases": []}
        for i in range(50)
    ]
    vectors = [[0.0, float(i)] for i in range(50)]
    await service.flush_entity_vectors(entries, vectors)

    assert len(qdrant.upsert_calls) == 1, "应只触发一次批量 Qdrant upsert"
    upserted_entries, upserted_vectors = qdrant.upsert_calls[0]
    assert len(upserted_entries) == 50
    assert len(upserted_vectors) == 50


@pytest.mark.asyncio
async def test_apply_resolution_no_longer_writes_qdrant_per_mention(tmp_path: Path) -> None:
    """回归保护：apply_resolution 不能再调 qdrant.upsert_entities（perf 杀手）。"""
    sqlite = SQLiteStore(str(tmp_path / "meta.db"))
    await sqlite.initialize()
    graph_store = GraphStore(str(tmp_path / "graph.pkl"))
    await graph_store.initialize()
    qdrant = MagicMock()
    qdrant.upsert_entities = MagicMock()
    service = GraphUpdateService(
        graph_store=graph_store, qdrant_store=qdrant, sqlite_store=sqlite,
    )

    doc_id = "doc-perf"
    await sqlite.create_document(
        doc_id=doc_id, title="perf", doc_type="web", source_uri="https://x.test"
    )
    chunk = ChunkRecord(
        chunk_id=f"{doc_id}-c1", doc_id=doc_id,
        content="alpha beta gamma", chunk_type="text",
        char_start=0, char_end=16, token_count=3,
    )
    await sqlite.upsert_chunks([chunk])

    mention = EntityMention(
        mention_id="m1", doc_id=doc_id, chunk_id=chunk.chunk_id,
        surface_form="Alpha", entity_type="method",
        char_start=0, char_end=5, context_snippet=chunk.content,
        vector=[0.1, 0.2],
    )
    await service.apply_resolution(
        mention=mention,
        result=ResolveResult(
            action="create", entity_id="e-alpha",
            canonical_name="Alpha", entity_type="method", score=0.0,
        ),
    )

    qdrant.upsert_entities.assert_not_called()
