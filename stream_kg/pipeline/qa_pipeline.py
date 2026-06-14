"""问答流水线（P1 先跑通向量检索）。

当前能力：
- query 路由（P1/P2 强制 vector，P3 才启用 graph router）；
- 向量检索；
- RAG 生成；
- citation 组装。
"""

from __future__ import annotations

from stream_kg.config import settings
from stream_kg.kg.models import Citation, QaResult, RetrievalMode
from stream_kg.retrieval.query_router import route_query
from stream_kg.retrieval.rag_generator import RagGenerator
from stream_kg.retrieval.reranker import RerankerClient
from stream_kg.retrieval.vector_search import VectorSearchService
from stream_kg.storage.sqlite_store import SQLiteStore


class QaPipeline:
    """检索上下文并生成带引用答案。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        vector_search: VectorSearchService,
        rag_generator: RagGenerator,
        reranker: RerankerClient | None = None,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.vector_search = vector_search
        self.rag_generator = rag_generator
        self.reranker = reranker

    async def run(self, *, query: str, retrieval_mode: RetrievalMode | None) -> QaResult:
        """执行一次问答。"""
        mode: RetrievalMode = retrieval_mode or route_query(query)

        # Phase 1/2：即便外部传了 hybrid，也强制退回 vector，避免误走未实现路径。
        if mode != "vector" and not settings.feature_graph_router_enabled:
            mode = "vector"

        top_k = settings.retrieval_top_k
        ranked_chunks = await self.vector_search.search(query=query, top_k=top_k)
        if self.reranker is not None and ranked_chunks:
            ranked_chunks = await self.reranker.rerank(
                query=query, chunks=ranked_chunks, top_k=settings.rerank_top_k
            )
        generated = await self.rag_generator.generate(query=query, chunks=ranked_chunks)

        # 将生成阶段返回的引用序号映射回真实 chunk/document 元信息。
        citations: list[Citation] = []
        used = generated["used_indexes"]
        for citation_index, source_index in enumerate(used, start=1):
            if source_index - 1 >= len(ranked_chunks):
                continue
            chunk = ranked_chunks[source_index - 1].chunk
            document = await self.sqlite_store.get_document(chunk.doc_id)
            citations.append(
                Citation(
                    citation_id=str(citation_index),
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.chunk_id,
                    doc_title=document.title if document else chunk.doc_id,
                    snippet=chunk.content[:200],
                    page_num=chunk.page_num,
                    section_title=chunk.section_title,
                )
            )

        return QaResult(
            answer=generated["answer"],
            citations=citations,
            retrieval_mode=mode,
            chunk_count=len(ranked_chunks),
            entity_count=0,
        )
