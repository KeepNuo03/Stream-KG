"""增量图更新服务。"""

from __future__ import annotations

import asyncio
from typing import Any

from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.models import EntityMention, ResolveResult, TemporalEdge
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore


class GraphUpdateService:
    """负责将消解结果与关系边写入图、SQLite 元数据与向量库。"""

    def __init__(
        self,
        *,
        graph_store: GraphStore,
        qdrant_store: QdrantStore,
        sqlite_store: SQLiteStore,
    ) -> None:
        self.graph_store = graph_store
        self.qdrant_store = qdrant_store
        self.sqlite_store = sqlite_store

    async def apply_resolution(self, *, mention: EntityMention, result: ResolveResult) -> None:
        """将单条 mention 的消解结果双写到图与 SQLite。

        注意：**不再** 在此处单条写 Qdrant 向量。Qdrant 单条 upsert + wait 在 Windows
        客户端环境实测 5s/条，119 mention 的文档要跑 10 分钟。改由上层在 mention loop
        结束后调用 ``flush_entity_vectors`` 批量入库。
        """
        mention.entity_id = result.entity_id
        await self.graph_store.upsert_entity_from_mention(
            entity_id=result.entity_id,
            canonical_name=result.canonical_name,
            entity_type=result.entity_type,
            mention=mention,
        )

        aliases = [mention.surface_form]
        await self.sqlite_store.upsert_entity(
            entity_id=result.entity_id,
            canonical_name=result.canonical_name,
            entity_type=result.entity_type,
            aliases=aliases,
            embedding_id=result.entity_id,
        )
        await self.sqlite_store.insert_entity_mention(mention)

    async def flush_entity_vectors(
        self, entries: list[dict[str, Any]], vectors: list[list[float]]
    ) -> None:
        """批量写入 entities collection（一次 PUT，10 分钟 -> 数秒）。

        entries 形如 ``{"entity_id", "canonical_name", "entity_type", "aliases"}``；
        index 与 vectors 一一对应。
        """
        if not entries:
            return
        await asyncio.to_thread(
            self.qdrant_store.upsert_entities, entries, vectors
        )

    async def add_edges(self, edges: list[TemporalEdge]) -> None:
        """批量写入关系边（图 + SQLite）。"""
        for edge in edges:
            await self.graph_store.add_temporal_edge(edge)
            await self.sqlite_store.upsert_temporal_edge(edge)
