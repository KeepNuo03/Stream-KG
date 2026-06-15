"""异步 ingestion 流水线（P1 核心主链路）。

主链路：
1) 将文档状态置为 processing；
2) 按类型解析（PDF: MinerU，Web: Trafilatura）；
3) chunk 切分；
4) embedding；
5) 元数据写 SQLite、向量写 Qdrant；
6) 成功置 ready，失败置 failed。
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse
from uuid import uuid4

from stream_kg.config import settings
from stream_kg.encoding.embedder import Embedder
from stream_kg.encoding.entity_extractor import (
    EntityExtractor,
    canonicalize_label,
    derive_entity_id,
    infer_entity_type,
)
from stream_kg.encoding.llm_extractor import LlmExtractor
from stream_kg.encoding.text_quality import is_usable_document_text
from stream_kg.ingestion.chunker import Chunker
from stream_kg.ingestion.pdf_parser import PdfParser
from stream_kg.ingestion.web_parser import WebParser
from stream_kg.kg.candidate_retrieval import CandidateRetrieval
from stream_kg.kg.graph_store import GraphStore
from stream_kg.kg.graph_update import GraphUpdateService
from stream_kg.kg.online_resolve import OnlineResolver
from stream_kg.kg.temporal_extract import TemporalExtractor
from stream_kg.kg.models import ResolveResult
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class IngestPipeline:
    """解析 -> 切分 -> 向量化 -> 存储 的编排器。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        graph_store: GraphStore,
        qdrant_store: QdrantStore,
        embedder: Embedder,
        llm_extractor: LlmExtractor | None = None,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.qdrant_store = qdrant_store
        self.embedder = embedder
        # Phase A.7：LLM 抽取器（lazy 注入；测试可传 mock；prod 默认从 settings 构造）。
        # 注意：构造 DeepSeekClient 不会真的调网络，只读 settings；
        # 即使 LLM_API_KEY 未配，LlmExtractor 也能实例化，调用时才报错。
        self._llm_extractor = llm_extractor
        self.pdf_parser = PdfParser(
            mineru_cli=settings.mineru_cli,
            output_dir=settings.mineru_output_dir,
            backend=settings.mineru_backend,
            device=settings.mineru_device,
            source=settings.mineru_source,
            timeout_sec=settings.mineru_timeout_sec,
            parse_mode=settings.pdf_parse_mode,
        )
        self.web_parser = WebParser(
            timeout_sec=settings.web_fetch_timeout_sec,
            cookie=settings.web_fetch_cookie,
        )
        self.chunker = Chunker(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
        self.entity_extractor = EntityExtractor()
        self.candidate_retrieval = CandidateRetrieval(qdrant_store=self.qdrant_store)
        self.online_resolver = OnlineResolver()
        self.graph_store = graph_store
        self.graph_update = GraphUpdateService(
            graph_store=self.graph_store,
            qdrant_store=self.qdrant_store,
            sqlite_store=self.sqlite_store,
        )
        self.temporal_extractor = TemporalExtractor()

    async def run(self, doc_id: str) -> None:
        """执行单文档入库。

        注意：
        - 本函数是后台任务入口；
        - 异常不抛给前端，而是落状态到 failed + error_message。
        """
        document = await self.sqlite_store.get_document(doc_id)
        if document is None:
            return

        try:
            started = time.perf_counter()
            await self.sqlite_store.set_document_status(doc_id, "processing", error_message=None)

            parse_started = time.perf_counter()
            if document.doc_type == "pdf":
                text, page_count = await asyncio.to_thread(self.pdf_parser.parse, document.source_uri)
            else:
                text, web_title = await asyncio.to_thread(self.web_parser.parse_with_meta, document.source_uri)
                page_count = None
                source_host = (urlparse(document.source_uri).hostname or "").strip()
                if web_title and (not document.title or document.title.strip() in {source_host, source_host.removeprefix("www.")}):
                    await self.sqlite_store.update_document_title(doc_id, web_title)
            logger.info("Ingest %s parse done in %.2fs", doc_id, time.perf_counter() - parse_started)

            chunk_started = time.perf_counter()
            chunks = self.chunker.split(doc_id=doc_id, text=text)
            chunks = [chunk for chunk in chunks if is_usable_document_text(chunk.content)]
            if not chunks:
                raise RuntimeError(
                    "文档解析结果不可用（乱码/占位页/文本过短）。"
                    "请换 PDF_PARSE_MODE=quality 重试，或检查源文件是否为扫描件。"
                )

            embed_probe = await self.embedder.probe_backend()
            if embed_probe.get("mode") in {"pseudo", "client_pseudo", "unknown", "down"}:
                logger.warning(
                    "Ingest %s: embedding backend is %s — retrieval quality will be poor until "
                    "embedding_server runs with a real model and documents are reprocessed.",
                    doc_id,
                    embed_probe.get("mode"),
                )

            vectors = await self.embedder.embed_batch([chunk.content for chunk in chunks])
            logger.info(
                "Ingest %s chunk/embed done (%d chunks) in %.2fs",
                doc_id,
                len(chunks),
                time.perf_counter() - chunk_started,
            )

            for chunk in chunks:
                chunk.embedding_id = chunk.chunk_id

            await self.sqlite_store.upsert_chunks(chunks)
            await asyncio.to_thread(self.qdrant_store.upsert_chunks, chunks, vectors)

            if settings.feature_kg_enabled:
                kg_started = time.perf_counter()
                if settings.feature_kg_use_llm:
                    # Phase A.7：LLM 路径只写 kg_extraction_logs + 更 doc.kg_status，
                    # 不上图（Phase B 接图谱写入）。
                    await self.run_llm_extraction(doc_id=doc_id)
                else:
                    # 旧规则路径：保留 incremental_kg 行为不变（E1 fallback）。
                    await self._run_incremental_kg(chunks=chunks)
                logger.info("Ingest %s kg done in %.2fs", doc_id, time.perf_counter() - kg_started)

            await self.sqlite_store.set_document_status(
                doc_id,
                "ready",
                page_count=page_count,
                error_message=None,
            )
            logger.info("Ingest %s ready in %.2fs total", doc_id, time.perf_counter() - started)
        except Exception as exc:
            # 所有异常统一落库，方便前端展示失败原因。
            await self.sqlite_store.set_document_status(doc_id, "failed", error_message=str(exc))

    async def _run_incremental_kg(self, *, chunks) -> None:
        """Phase 3 (R-020 重写)：抽取 mention → 确定性消解 → 共现累加 → 入图。

        关键改动：
        - 抛弃「向量检索 + composite_score 阈值」消解，改用 `canonicalize_label`
          产出的确定性 entity_id。同名实体在同 ingestion 内立刻合并，跨 ingestion
          也合并（因为 ID 只跟 canonical+type 有关），mention_count 真正可累加。
        - mention 向量仍生成，作为 entities collection 的 anchor（保留候选检索能力，
          但消解逻辑不再依赖它，避免 R-020 的"永远 create"问题）。
        """
        await self.graph_store.initialize()
        mentions = await asyncio.to_thread(self.entity_extractor.extract, chunks)
        if settings.kg_max_mentions_per_doc > 0 and len(mentions) > settings.kg_max_mentions_per_doc:
            mentions = mentions[: settings.kg_max_mentions_per_doc]
        if not mentions:
            await self.graph_store.persist()
            return

        # 给每个 mention 立刻打上确定性 entity_id（避免 OnlineResolver 把同名拆成多个）。
        for mention in mentions:
            canonical = canonicalize_label(mention.surface_form)
            ent_type = infer_entity_type(mention.surface_form)
            if not canonical:
                continue
            mention.entity_type = ent_type
            mention.entity_id = derive_entity_id(canonical, ent_type)

        mention_texts = [f"{mention.surface_form} | {mention.context_snippet}" for mention in mentions]
        mention_vectors = await self.embedder.embed_batch(mention_texts)
        for mention, vector in zip(mentions, mention_vectors, strict=True):
            mention.vector = vector

        # 直接构造 ResolveResult（action=create 只为兼容下游 vector 写入）。
        seen_entity_ids: set[str] = set()
        for mention in mentions:
            if not mention.entity_id:
                continue
            canonical = canonicalize_label(mention.surface_form)
            ent_type = infer_entity_type(mention.surface_form)
            is_new = mention.entity_id not in seen_entity_ids
            seen_entity_ids.add(mention.entity_id)
            result = ResolveResult(
                action="create" if is_new else "merge",
                entity_id=mention.entity_id,
                canonical_name=mention.surface_form,
                entity_type=ent_type,
                score=1.0,
            )
            await self.graph_update.apply_resolution(mention=mention, result=result)

        temporal_edges = self.temporal_extractor.extract(mentions, chunks)
        await self.graph_update.add_edges(temporal_edges)
        await self.graph_store.persist()

    # ==========================================================================
    # P3-X · Phase A.7：LLM-based KG 抽取（不上图，只写 logs + 更 doc.kg_status）
    # ==========================================================================

    @property
    def llm_extractor(self) -> LlmExtractor:
        """Lazy 构造 LlmExtractor。

        延迟到首次访问才构造，避免没用到 LLM 路径的用户在 startup 阶段
        必须备好 prompts/kg_extraction.txt 和 LLM_API_KEY。
        """
        if self._llm_extractor is None:
            self._llm_extractor = LlmExtractor()
        return self._llm_extractor

    async def run_llm_extraction(self, *, doc_id: str) -> dict:
        """对单个文档跑 LLM KG 抽取，写 logs + 更 doc.kg_status。

        返回汇总 dict（chunks 总数 / ok / failed / token / cost），
        供 API 层 / 监控直接使用。

        **Phase A 范围**：
        - 调 LlmExtractor.extract_batch 拿到结果
        - 每个 chunk 写一条 kg_extraction_logs
        - doc.kg_status 流转 unprocessed/ready → extracting → ready/failed
        - **不上图**（图谱写入是 Phase B 的 GraphStore.incremental_add_extraction）

        失败语义：
        - 全部 chunk 都失败 → doc.kg_status='failed'
        - 部分 chunk 失败 → doc.kg_status='ready'（监控统计可看 failed_count）
        - 文档不存在 / 无 chunk → 返回空 summary、不改 status
        """
        document = await self.sqlite_store.get_document(doc_id)
        if document is None:
            logger.warning("run_llm_extraction: doc %s 不存在", doc_id)
            return {"total": 0, "ok": 0, "failed": 0}

        chunks = await self.sqlite_store.list_chunks_by_doc(doc_id)
        if not chunks:
            logger.info("run_llm_extraction: doc %s 没有 chunk，跳过", doc_id)
            return {"total": 0, "ok": 0, "failed": 0}

        await self.sqlite_store.set_document_kg_status(doc_id, "extracting", error_message=None)

        started = time.perf_counter()
        try:
            summary = await self.llm_extractor.extract_batch(
                [(c.chunk_id, c.content) for c in chunks]
            )
        except Exception as exc:  # 极端情况（如 prompt 文件丢失）兜底
            logger.exception("run_llm_extraction: doc %s 抽取过程意外异常", doc_id)
            await self.sqlite_store.set_document_kg_status(
                doc_id, "failed", error_message=f"extract_batch crashed: {exc!r}"
            )
            return {"total": len(chunks), "ok": 0, "failed": len(chunks)}

        # 逐 chunk 落 logs（顺序写避免 sqlite 并发写竞争）
        for result in summary.results:
            await self.sqlite_store.insert_kg_extraction_log(
                log_id=str(uuid4()),
                chunk_id=result.chunk_id,
                doc_id=doc_id,
                status="ok" if result.success else "failed",
                attempt_count=result.attempt.retries + 1,
                elapsed_sec=result.attempt.elapsed_sec,
                prompt_tokens=result.attempt.prompt_tokens,
                completion_tokens=result.attempt.completion_tokens,
                cost_yuan=result.attempt.cost_yuan,
                entities_count=(
                    len(result.extraction.entities) if result.extraction else 0
                ),
                relations_count=(
                    len(result.extraction.relations) if result.extraction else 0
                ),
                error_message=result.attempt.error,
                # 失败时保留 raw_output 供 debug；成功就别灌满库（每条 raw 可能上 KB）
                raw_output=result.attempt.raw_output if not result.success else None,
            )

        # 终态
        if summary.ok_chunks == 0:
            await self.sqlite_store.set_document_kg_status(
                doc_id,
                "failed",
                error_message=f"all {summary.total_chunks} chunks failed",
            )
        else:
            await self.sqlite_store.set_document_kg_status(doc_id, "ready", error_message=None)

        elapsed = time.perf_counter() - started
        logger.info(
            "run_llm_extraction doc=%s total=%d ok=%d failed=%d cost=%.4fCNY elapsed=%.2fs",
            doc_id,
            summary.total_chunks,
            summary.ok_chunks,
            summary.failed_chunks,
            summary.total_cost_yuan,
            elapsed,
        )
        return {
            "doc_id": doc_id,
            "total": summary.total_chunks,
            "ok": summary.ok_chunks,
            "failed": summary.failed_chunks,
            "elapsed_sec": elapsed,
            "total_prompt_tokens": summary.total_prompt_tokens,
            "total_completion_tokens": summary.total_completion_tokens,
            "total_cost_yuan": summary.total_cost_yuan,
        }
