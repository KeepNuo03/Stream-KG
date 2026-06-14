"""P2 验收 smoke：两篇文档入图规模 + 删除后级联一致（离线模拟）。

用法：
    uv run --extra dev python scripts/p2_acceptance_smoke.py
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path

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


async def _ingest_doc(
    *,
    sqlite: SQLiteStore,
    graph_update: GraphUpdateService,
    doc_id: str,
    title: str,
    entities: list[str],
) -> None:
    await sqlite.create_document(doc_id=doc_id, title=title, doc_type="web", source_uri=f"https://example.com/{doc_id}")
    content = f"{entities[0]} improves {entities[1]} and extends Transformer research."
    chunk = ChunkRecord(
        chunk_id=f"chunk-{doc_id}",
        doc_id=doc_id,
        content=content,
        chunk_type="text",
        char_start=0,
        char_end=len(content),
        token_count=12,
    )
    await sqlite.upsert_chunks([chunk])

    left = EntityMention(
        mention_id=f"m-{doc_id}-1",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form=entities[0],
        entity_type="method",
        char_start=0,
        char_end=len(entities[0]),
        context_snippet=content,
        vector=[0.1, 0.2, 0.3],
    )
    right = EntityMention(
        mention_id=f"m-{doc_id}-2",
        doc_id=doc_id,
        chunk_id=chunk.chunk_id,
        surface_form=entities[1],
        entity_type="method",
        char_start=content.index(entities[1]),
        char_end=content.index(entities[1]) + len(entities[1]),
        context_snippet=content,
        vector=[0.2, 0.3, 0.4],
    )
    await graph_update.apply_resolution(
        mention=left,
        result=ResolveResult(
            action="create",
            entity_id=f"entity-{entities[0].lower()}",
            canonical_name=entities[0],
            entity_type="method",
            score=0.0,
        ),
    )
    await graph_update.apply_resolution(
        mention=right,
        result=ResolveResult(
            action="create",
            entity_id=f"entity-{entities[1].lower()}",
            canonical_name=entities[1],
            entity_type="method",
            score=0.0,
        ),
    )
    await graph_update.add_edges(
        [
            TemporalEdge(
                edge_id=new_edge_id(),
                head_entity_id=f"entity-{entities[0].lower()}",
                tail_entity_id=f"entity-{entities[1].lower()}",
                relation_type="improves",
                confidence=0.82,
                evidence_chunk_id=chunk.chunk_id,
                created_at=datetime.now(UTC),
            )
        ]
    )


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sqlite = SQLiteStore(str(root / "meta.db"))
        await sqlite.initialize()
        graph_store = GraphStore(str(root / "graph.pkl"))
        await graph_store.initialize()
        graph_update = GraphUpdateService(
            graph_store=graph_store,
            qdrant_store=_NoopQdrant(),  # type: ignore[arg-type]
            sqlite_store=sqlite,
        )
        cleanup = KgCleanupService(
            sqlite_store=sqlite,
            graph_store=graph_store,
            qdrant_store=_NoopQdrant(),  # type: ignore[arg-type]
        )

        await _ingest_doc(
            sqlite=sqlite,
            graph_update=graph_update,
            doc_id="doc-1",
            title="Doc One",
            entities=["Redis", "Qdrant"],
        )
        await _ingest_doc(
            sqlite=sqlite,
            graph_update=graph_update,
            doc_id="doc-2",
            title="Doc Two",
            entities=["Redis", "Transformer"],
        )
        await graph_store.persist()

        exported = await graph_store.export_graph()
        node_count = exported["stats"]["node_count"]
        edge_count = exported["stats"]["edge_count"]
        assert node_count >= 3, f"节点规模不足: {node_count}"
        assert edge_count >= 2, f"边规模不足: {edge_count}"
        print(f"[OK] 入图规模：nodes={node_count}, edges={edge_count}")

        await cleanup.cleanup_document("doc-1")
        await cleanup.cleanup_document("doc-2")
        stats = await sqlite.stats()
        assert stats["documents"] == 0
        assert stats["chunks"] == 0
        assert stats["entities"] == 0
        assert stats["edges"] == 0
        exported_after = await graph_store.export_graph()
        assert exported_after["stats"]["node_count"] == 0
        print("[OK] 删除后 SQLite / graph.pkl 一致")


if __name__ == "__main__":
    asyncio.run(main())
