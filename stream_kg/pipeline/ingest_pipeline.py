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

from stream_kg.config import settings
from stream_kg.encoding.embedder import Embedder
from stream_kg.encoding.entity_extractor import (
    EntityExtractor,
    canonicalize_label,
    derive_entity_id,
    infer_entity_type,
)
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
    ) -> None:
        self.sqlite_store = sqlite_store
        self.qdrant_store = qdrant_store
        self.embedder = embedder
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
