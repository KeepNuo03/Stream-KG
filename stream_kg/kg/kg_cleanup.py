"""知识图谱级联清理服务（P2）。

删除文档时同步清理：
- graph.pkl 中该文档相关节点/边；
- 无 mention 残留的孤立实体及其 Qdrant 向量。
"""

from __future__ import annotations

import asyncio
import logging

from stream_kg.kg.graph_store import GraphStore
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class KgCleanupService:
    """文档删除后的图谱一致性维护。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        graph_store: GraphStore,
        qdrant_store: QdrantStore,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.graph_store = graph_store
        self.qdrant_store = qdrant_store

    async def cleanup_document(self, doc_id: str) -> None:
        """按文档 ID 清理图谱、SQLite 元数据与实体向量（删除文档场景）。"""
        chunks = await self.sqlite_store.list_chunks_by_doc(doc_id)
        chunk_ids = [chunk.chunk_id for chunk in chunks]

        await self.graph_store.initialize()
        removed_entity_ids = await self.graph_store.remove_document(doc_id, chunk_ids=chunk_ids)

        await self.sqlite_store.delete_document(doc_id)

        await self._purge_entities_without_mentions(removed_entity_ids)
        asyncio.create_task(self.graph_store.persist())

    async def cleanup_for_reprocess(self, doc_id: str) -> None:
        """重处理前清掉与文档相关的 KG 数据，**保留** documents 行。

        步骤：
        1) 图谱：移除 L0 节点 + L0-L1 边 + 孤立 L1
        2) Qdrant：清掉本文档的 chunk 向量
        3) SQLite：清掉 chunks / doc_entity_links / kg_extraction_logs
           - chunks 删除会级联清 entity_mentions / temporal_edges / kg_extraction_logs
           - doc_entity_links 由 doc_id FK 关联（不依赖 chunks），需单独清
        4) 复位 kg_status='unprocessed'
        5) 清理孤立实体（在 SQLite + Qdrant）

        避免「重处理 -> 数据翻倍」（首次 16 chunks，重试再加 16 -> 32）。
        """
        chunks = await self.sqlite_store.list_chunks_by_doc(doc_id)
        chunk_ids = [chunk.chunk_id for chunk in chunks]

        await self.graph_store.initialize()
        removed_entity_ids = await self.graph_store.remove_document(doc_id, chunk_ids=chunk_ids)

        await asyncio.to_thread(self.qdrant_store.delete_chunks_by_doc, doc_id)

        await self.sqlite_store.delete_doc_entity_links_by_doc(doc_id)
        await self.sqlite_store.delete_kg_extraction_logs_by_doc(doc_id)
        # chunks 必须最后删，否则上面两表的 chunk 引用会被级联，但反正都要删
        await self.sqlite_store.delete_chunks_by_doc(doc_id)

        await self.sqlite_store.set_document_kg_status(
            doc_id, "unprocessed", error_message=None
        )

        await self._purge_entities_without_mentions(removed_entity_ids)
        asyncio.create_task(self.graph_store.persist())

    async def _purge_entities_without_mentions(self, entity_ids: list[str]) -> None:
        """仅清理候选实体中已无 mention 的项（避免每次全表扫 orphan）。"""
        to_delete = await self.sqlite_store.filter_entities_without_mentions(entity_ids)
        for entity_id in to_delete:
            try:
                await asyncio.to_thread(self.qdrant_store.delete_entity, entity_id)
            except Exception as exc:
                logger.warning("Failed to delete entity vector %s: %s", entity_id, exc)
            await self.sqlite_store.delete_entity(entity_id)
