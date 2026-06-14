"""依赖注入工厂。

使用 `lru_cache(maxsize=1)` 保证：
- 进程内单例复用，避免重复创建连接与服务对象；
- 路由层只关心“拿到可用服务”，不关心构造细节。
"""

from __future__ import annotations

from functools import lru_cache

from stream_kg.config import settings
from stream_kg.encoding.embedder import Embedder
from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.kg_cleanup import KgCleanupService
from stream_kg.pipeline.ingest_pipeline import IngestPipeline
from stream_kg.pipeline.qa_pipeline import QaPipeline
from stream_kg.retrieval.rag_generator import RagGenerator
from stream_kg.retrieval.reranker import RerankerClient
from stream_kg.retrieval.vector_search import VectorSearchService
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore


@lru_cache(maxsize=1)
def get_sqlite_store() -> SQLiteStore:
    """获取 SQLite 元数据存储单例。"""
    return SQLiteStore(settings.sqlite_path)


@lru_cache(maxsize=1)
def get_qdrant_store() -> QdrantStore:
    """获取 Qdrant 存储单例。"""
    return QdrantStore(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        chunks_collection=settings.qdrant_collection_chunks,
        entities_collection=settings.qdrant_collection_entities,
        vector_size=settings.embedding_dim,
    )


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """获取 Embedding 客户端单例。"""
    return Embedder(
        server_url=settings.embedding_server_url,
        dim=settings.embedding_dim,
        timeout_sec=settings.embedding_request_timeout_sec,
        connect_timeout_sec=settings.embedding_connect_timeout_sec,
        fallback_cooldown_sec=settings.embedding_fallback_cooldown_sec,
        batch_size=settings.embedding_batch_size,
    )


@lru_cache(maxsize=1)
def get_reranker() -> RerankerClient:
    """获取 Reranker 客户端单例（仅当 feature flag 开启时才真正调 server）。"""
    return RerankerClient(
        server_url=settings.reranker_server_url,
        enabled=settings.feature_reranker_enabled,
        timeout_sec=settings.reranker_request_timeout_sec,
        connect_timeout_sec=settings.reranker_connect_timeout_sec,
        fallback_cooldown_sec=settings.reranker_fallback_cooldown_sec,
        max_pairs_per_call=settings.reranker_max_pairs_per_call,
        instruction=settings.reranker_instruction,
    )


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStore:
    """获取 GraphStore 单例。"""
    return GraphStore(settings.graph_path)


@lru_cache(maxsize=1)
def get_kg_cleanup_service() -> KgCleanupService:
    """获取 KG 级联清理服务单例。"""
    return KgCleanupService(
        sqlite_store=get_sqlite_store(),
        graph_store=get_graph_store(),
        qdrant_store=get_qdrant_store(),
    )


@lru_cache(maxsize=1)
def get_ingest_pipeline() -> IngestPipeline:
    """获取 ingestion 流水线单例。"""
    return IngestPipeline(
        sqlite_store=get_sqlite_store(),
        graph_store=get_graph_store(),
        qdrant_store=get_qdrant_store(),
        embedder=get_embedder(),
    )


@lru_cache(maxsize=1)
def get_qa_pipeline() -> QaPipeline:
    """获取 QA 流水线单例。"""
    sqlite_store = get_sqlite_store()
    vector_search = VectorSearchService(
        sqlite_store=sqlite_store,
        qdrant_store=get_qdrant_store(),
        embedder=get_embedder(),
    )
    rag_generator = RagGenerator(sqlite_store=sqlite_store)
    return QaPipeline(
        sqlite_store=sqlite_store,
        vector_search=vector_search,
        rag_generator=rag_generator,
        reranker=get_reranker(),
    )
