"""向量检索服务。

流程：
1) 将 query 转成 embedding；
2) 在 Qdrant 检索 top-k；
3) 按 chunk_id 回查 SQLite，补齐 chunk 内容；
4) 返回带分数的 RetrievalChunk 列表。
"""

from __future__ import annotations

import asyncio

from stream_kg.encoding.embedder import Embedder
from stream_kg.kg.models import RetrievalChunk
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore


class VectorSearchService:
    """封装 dense retrieval 逻辑。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        qdrant_store: QdrantStore,
        embedder: Embedder,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.qdrant_store = qdrant_store
        self.embedder = embedder

    async def search(self, query: str, top_k: int = 10) -> list[RetrievalChunk]:
        """检索并返回排序后的 chunk。"""
        query_vector = await self.embedder.embed_one(query)
        # Qdrant Python 客户端是同步调用，放线程池避免阻塞事件循环。
        results = await asyncio.to_thread(
            self.qdrant_store.search_chunks,
            query_vector,
            top_k,
        )

        chunk_ids = [str(item["chunk_id"]) for item in results]
        chunk_map = await self.sqlite_store.get_chunks_by_ids(chunk_ids)

        # 仅返回在 SQLite 中仍可查到的 chunk，防止向量与元数据短暂不一致。
        ranked: list[RetrievalChunk] = []
        for item in results:
            chunk_id = str(item["chunk_id"])
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue
            ranked.append(RetrievalChunk(chunk=chunk, score=float(item["score"])))
        return ranked
