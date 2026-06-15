"""Unit tests for IngestPipeline.run_llm_extraction (P3-X · Phase A.7)。

策略：
- 真 SQLiteStore（在 tmp 文件，验证 logs/status 真落库）
- 其他依赖（graph_store/qdrant_store/embedder）一律用 MagicMock 占位（init 不调用）
- LlmExtractor 用 AsyncMock 注入 extract_batch
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stream_kg.encoding.llm_extractor import (
    BatchExtractionSummary,
    ChunkExtractionResult,
    ExtractionAttempt,
)
from stream_kg.kg.llm_models import KgExtraction, LlmEntity, LlmRelation
from stream_kg.kg.models import ChunkRecord
from stream_kg.pipeline.ingest_pipeline import IngestPipeline
from stream_kg.storage.sqlite_store import SQLiteStore


# ---------- fixtures ----------


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteStore:
    s = SQLiteStore(str(tmp_path / "kg.db"))
    await s.initialize()
    return s


@pytest.fixture
def fake_llm_extractor() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def pipeline(store: SQLiteStore, fake_llm_extractor: AsyncMock) -> IngestPipeline:
    """构造 IngestPipeline，依赖 sqlite_store 真实 + 其他 mock。"""
    return IngestPipeline(
        sqlite_store=store,
        graph_store=MagicMock(),
        qdrant_store=MagicMock(),
        embedder=MagicMock(),
        llm_extractor=fake_llm_extractor,
    )


# ---------- helpers ----------


async def _seed_doc_and_chunks(
    store: SQLiteStore, doc_id: str, n_chunks: int
) -> list[ChunkRecord]:
    await store.create_document(
        doc_id=doc_id, title="Doc", doc_type="pdf", source_uri="file:///x.pdf"
    )
    chunks = [
        ChunkRecord(
            chunk_id=f"{doc_id}-c{i}",
            doc_id=doc_id,
            content=f"chunk text {i}",
            chunk_type="text",
            char_start=i * 100,
            char_end=i * 100 + 50,
            token_count=10,
        )
        for i in range(n_chunks)
    ]
    await store.upsert_chunks(chunks)
    return chunks


def _ok_result(chunk_id: str, n_ent: int = 2, n_rel: int = 1) -> ChunkExtractionResult:
    ext = KgExtraction(
        entities=[
            LlmEntity(name=f"E{i}", type="method", salience=0.8) for i in range(n_ent)
        ],
        relations=[
            LlmRelation(head="E0", relation="uses", tail="E1", confidence=0.7)
            for _ in range(n_rel)
        ],
    )
    return ChunkExtractionResult(
        chunk_id=chunk_id,
        extraction=ext,
        attempt=ExtractionAttempt(
            success=True,
            retries=0,
            elapsed_sec=1.5,
            prompt_tokens=100,
            completion_tokens=200,
            cost_yuan=0.0004,
            raw_output='{"entities":[],"relations":[]}',
        ),
    )


def _fail_result(chunk_id: str, error: str = "HTTP 500") -> ChunkExtractionResult:
    return ChunkExtractionResult(
        chunk_id=chunk_id,
        extraction=None,
        attempt=ExtractionAttempt(
            success=False,
            retries=2,
            elapsed_sec=8.0,
            prompt_tokens=120,
            completion_tokens=0,
            cost_yuan=0.00006,
            error=error,
            raw_output=None,
        ),
    )


def _summary(results: list[ChunkExtractionResult]) -> BatchExtractionSummary:
    ok = sum(1 for r in results if r.success)
    return BatchExtractionSummary(
        total_chunks=len(results),
        ok_chunks=ok,
        failed_chunks=len(results) - ok,
        elapsed_sec=2.5,
        total_prompt_tokens=sum(r.attempt.prompt_tokens for r in results),
        total_completion_tokens=sum(r.attempt.completion_tokens for r in results),
        total_cost_yuan=sum(r.attempt.cost_yuan for r in results),
        results=results,
    )


# ---------- tests ----------


async def test_run_llm_extraction_all_success(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    chunks = await _seed_doc_and_chunks(store, "d1", 3)
    fake_llm_extractor.extract_batch.return_value = _summary(
        [_ok_result(c.chunk_id) for c in chunks]
    )

    result = await pipeline.run_llm_extraction(doc_id="d1")

    assert result["total"] == 3
    assert result["ok"] == 3
    assert result["failed"] == 0
    # doc.kg_status='ready'
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "ready"
    assert doc.kg_error_message is None

    # 3 条 ok logs
    stats = await store.get_kg_extraction_stats(doc_id="d1")
    assert stats["total"] == 3
    assert stats["ok_count"] == 3
    assert stats["failed_count"] == 0
    assert stats["total_entities"] == 6  # 2 ent × 3 chunk
    assert stats["total_relations"] == 3


async def test_run_llm_extraction_all_failure_sets_doc_failed(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    chunks = await _seed_doc_and_chunks(store, "d1", 2)
    fake_llm_extractor.extract_batch.return_value = _summary(
        [_fail_result(c.chunk_id, "HTTP 503") for c in chunks]
    )

    result = await pipeline.run_llm_extraction(doc_id="d1")

    assert result["ok"] == 0
    assert result["failed"] == 2
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "failed"
    assert "all 2 chunks failed" in (doc.kg_error_message or "")


async def test_run_llm_extraction_partial_failure_keeps_ready(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    chunks = await _seed_doc_and_chunks(store, "d1", 3)
    fake_llm_extractor.extract_batch.return_value = _summary(
        [_ok_result(chunks[0].chunk_id), _fail_result(chunks[1].chunk_id), _ok_result(chunks[2].chunk_id)]
    )

    result = await pipeline.run_llm_extraction(doc_id="d1")

    assert result["ok"] == 2
    assert result["failed"] == 1
    # 至少有 1 个 ok → 整体 ready，监控统计可见 failed_count
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "ready"

    stats = await store.get_kg_extraction_stats(doc_id="d1")
    assert stats["ok_count"] == 2
    assert stats["failed_count"] == 1


async def test_run_llm_extraction_status_flows_through_extracting(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    """验证 status 中间态：调用时应短暂经过 extracting。

    用 side_effect 在 extract_batch 内部偷看 doc.kg_status 来断言。
    """
    chunks = await _seed_doc_and_chunks(store, "d1", 1)
    observed: dict[str, str] = {}

    async def _peek(_chunks_arg, **_kw):
        doc = await store.get_document("d1")
        assert doc is not None
        observed["mid"] = doc.kg_status
        return _summary([_ok_result(chunks[0].chunk_id)])

    fake_llm_extractor.extract_batch.side_effect = _peek

    await pipeline.run_llm_extraction(doc_id="d1")

    assert observed["mid"] == "extracting"
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "ready"


async def test_run_llm_extraction_no_chunks_returns_empty(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )

    result = await pipeline.run_llm_extraction(doc_id="d1")

    assert result == {"total": 0, "ok": 0, "failed": 0}
    fake_llm_extractor.extract_batch.assert_not_awaited()
    # status 不应被改（仍是初始 unprocessed）
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "unprocessed"


async def test_run_llm_extraction_missing_doc_returns_empty(
    pipeline: IngestPipeline, fake_llm_extractor: AsyncMock
) -> None:
    result = await pipeline.run_llm_extraction(doc_id="missing-doc")
    assert result == {"total": 0, "ok": 0, "failed": 0}
    fake_llm_extractor.extract_batch.assert_not_awaited()


async def test_run_llm_extraction_extractor_crash_sets_failed(
    pipeline: IngestPipeline, store: SQLiteStore, fake_llm_extractor: AsyncMock
) -> None:
    """extract_batch 整体抛异常（如 prompt 文件丢失）→ doc.kg_status='failed'。"""
    await _seed_doc_and_chunks(store, "d1", 2)
    fake_llm_extractor.extract_batch.side_effect = RuntimeError("prompt missing!")

    result = await pipeline.run_llm_extraction(doc_id="d1")

    assert result["ok"] == 0
    assert result["failed"] == 2
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "failed"
    assert "prompt missing" in (doc.kg_error_message or "")
