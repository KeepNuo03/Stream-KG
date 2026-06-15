"""Qdrant 向量存储封装。

职责：
- 初始化 chunks/entities 两个 collection；
- 写入 chunk 向量与 payload；
- 执行向量相似度检索；
- 按 doc_id 删除对应向量。
"""

from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from stream_kg.kg.models import ChunkRecord
from stream_kg.storage.qdrant_point_id import entity_id_to_qdrant_point_id


class QdrantStore:
    """Qdrant 操作聚合器。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        chunks_collection: str,
        entities_collection: str,
        vector_size: int,
    ) -> None:
        self.host = host
        self.port = port
        self.chunks_collection = chunks_collection
        self.entities_collection = entities_collection
        self.vector_size = vector_size
        self.client = QdrantClient(host=self.host, port=self.port)

    def initialize(self) -> None:
        """确保必需 collection 已创建。"""
        self._ensure_collection(self.chunks_collection)
        self._ensure_collection(self.entities_collection)

    def _ensure_collection(self, name: str) -> None:
        """按 collection 名检查并创建（不存在才创建）。"""
        collections = self.client.get_collections().collections
        existing = {collection.name for collection in collections}
        if name in existing:
            return
        self.client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=self.vector_size,
                distance=models.Distance.COSINE,
            ),
        )

    def health(self) -> bool:
        """健康探活：能读取 collections 即视为可用。"""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False

    def upsert_chunks(self, chunks: list[ChunkRecord], vectors: list[list[float]]) -> None:
        """批量写入 chunk 向量和检索 payload。"""
        if not chunks:
            return
        points: list[models.PointStruct] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=chunk.chunk_id,
                    vector=vector,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "doc_id": chunk.doc_id,
                        "page_num": chunk.page_num,
                        "section_title": chunk.section_title,
                        "content_preview": chunk.content[:200],
                    },
                )
            )
        # 以 chunk_id 作为 point id，便于后续从检索结果直接回查 SQLite chunk。
        self.client.upsert(collection_name=self.chunks_collection, points=points)

    def search_chunks(self, query_vector: list[float], top_k: int = 10) -> list[dict[str, Any]]:
        """向量检索 top-k chunk。"""
        # 兼容 Qdrant 新旧客户端：
        # - 旧版本使用 search(query_vector=...)
        # - 新版本使用 query_points(query=...)
        if hasattr(self.client, "search"):
            results = self.client.search(
                collection_name=self.chunks_collection,
                query_vector=query_vector,
                limit=top_k,
            )
        else:
            query_result = self.client.query_points(
                collection_name=self.chunks_collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            )
            results = query_result.points if hasattr(query_result, "points") else query_result
        return [
            {
                "chunk_id": str(point.id),
                "score": float(point.score),
                "payload": point.payload or {},
            }
            for point in results
        ]

    def upsert_entities(self, entities: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        """批量写入实体向量和 payload。"""
        if not entities:
            return
        points: list[models.PointStruct] = []
        for entity, vector in zip(entities, vectors, strict=True):
            entity_id = str(entity.get("entity_id") or "")
            if not entity_id:
                continue
            points.append(
                models.PointStruct(
                    id=entity_id_to_qdrant_point_id(entity_id),
                    vector=vector,
                    payload={
                        "entity_id": entity_id,
                        "canonical_name": str(entity.get("canonical_name") or ""),
                        "entity_type": str(entity.get("entity_type") or "concept"),
                        "aliases": entity.get("aliases") or [],
                    },
                )
            )
        if not points:
            return
        self.client.upsert(collection_name=self.entities_collection, points=points)

    def search_entities(self, query_vector: list[float], top_k: int = 20) -> list[dict[str, Any]]:
        """向量检索实体候选。"""
        if hasattr(self.client, "search"):
            results = self.client.search(
                collection_name=self.entities_collection,
                query_vector=query_vector,
                limit=top_k,
            )
        else:
            query_result = self.client.query_points(
                collection_name=self.entities_collection,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            )
            results = query_result.points if hasattr(query_result, "points") else query_result
        rows: list[dict[str, Any]] = []
        for point in results:
            payload = point.payload or {}
            logical_id = str(payload.get("entity_id") or point.id)
            rows.append(
                {
                    "entity_id": logical_id,
                    "score": float(point.score),
                    "payload": payload,
                }
            )
        return rows

    def delete_entity(self, entity_id: str) -> None:
        """删除单实体向量。"""
        self.client.delete(
            collection_name=self.entities_collection,
            points_selector=models.PointIdsList(
                points=[entity_id_to_qdrant_point_id(entity_id)]
            ),
            wait=False,
        )

    def delete_chunks_by_doc(self, doc_id: str) -> None:
        """按文档 ID 删除该文档下所有 chunk 向量。"""
        self.client.delete(
            collection_name=self.chunks_collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            # 删除请求不阻塞等待 Qdrant 后台完成，优先保证前端删除交互响应速度。
            # 即便存在极短暂延迟，SQLite 已删除的 chunk 也会在检索回查阶段被过滤掉。
            wait=False,
        )
