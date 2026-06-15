"""Integration tests for documents.extract_kg / kg-stats endpoints
(P3-X · Phase A.8)。

策略：
- minimal FastAPI app，仅挂 documents router，避开 api.main 的 startup hook
  （它会触发真实 embedder.probe_backend 网络调用）
- monkeypatch get_sqlite_store / get_ingest_pipeline 注入 mock
- 用真 SQLiteStore + MagicMock IngestPipeline，验证 HTTP + 状态机
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import documents as docs_mod
from stream_kg.storage.sqlite_store import SQLiteStore


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteStore:
    s = SQLiteStore(str(tmp_path / "kg.db"))
    await s.initialize()
    return s


@pytest.fixture
def fake_pipeline() -> MagicMock:
    p = MagicMock()
    p.run_llm_extraction = AsyncMock(
        return_value={"total": 1, "ok": 1, "failed": 0}
    )
    return p


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    store: SQLiteStore,
    fake_pipeline: MagicMock,
) -> TestClient:
    monkeypatch.setattr(docs_mod, "get_sqlite_store", lambda: store)
    monkeypatch.setattr(docs_mod, "get_ingest_pipeline", lambda: fake_pipeline)
    monkeypatch.setattr(docs_mod, "get_kg_cleanup_service", lambda: MagicMock())
    monkeypatch.setattr(docs_mod, "get_qdrant_store", lambda: MagicMock())

    app = FastAPI()
    app.include_router(docs_mod.router, prefix="/api/v1/documents")
    return TestClient(app)


# ---------- POST /extract-kg ----------


async def test_extract_kg_404_when_doc_missing(
    client: TestClient, fake_pipeline: MagicMock
) -> None:
    resp = client.post("/api/v1/documents/missing/extract-kg")
    assert resp.status_code == 404
    fake_pipeline.run_llm_extraction.assert_not_awaited()


async def test_extract_kg_409_when_ingestion_not_ready(
    client: TestClient, store: SQLiteStore, fake_pipeline: MagicMock
) -> None:
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    # 默认 status='pending'（未 ingestion 完成）
    resp = client.post("/api/v1/documents/d1/extract-kg")
    assert resp.status_code == 409
    assert "not ready" in resp.json()["detail"]
    fake_pipeline.run_llm_extraction.assert_not_awaited()


async def test_extract_kg_409_when_already_extracting(
    client: TestClient, store: SQLiteStore, fake_pipeline: MagicMock
) -> None:
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    await store.set_document_status("d1", "ready")
    await store.set_document_kg_status("d1", "extracting")

    resp = client.post("/api/v1/documents/d1/extract-kg")
    assert resp.status_code == 409
    assert "already in progress" in resp.json()["detail"]
    fake_pipeline.run_llm_extraction.assert_not_awaited()


async def test_extract_kg_happy_path_triggers_background_task(
    client: TestClient, store: SQLiteStore, fake_pipeline: MagicMock
) -> None:
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    await store.set_document_status("d1", "ready")

    resp = client.post("/api/v1/documents/d1/extract-kg")
    assert resp.status_code == 202
    body = resp.json()
    assert body["doc_id"] == "d1"
    assert body["kg_status"] == "extracting"
    assert "queued" in body["message"].lower()

    # BackgroundTasks 在 TestClient 内会被同步执行
    fake_pipeline.run_llm_extraction.assert_awaited_once_with(doc_id="d1")


async def test_extract_kg_allows_retry_after_failure(
    client: TestClient, store: SQLiteStore, fake_pipeline: MagicMock
) -> None:
    """kg_status='failed' 时应允许再次触发（不像 'extracting' 被 409 挡掉）。"""
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    await store.set_document_status("d1", "ready")
    await store.set_document_kg_status("d1", "failed", error_message="prev fail")

    resp = client.post("/api/v1/documents/d1/extract-kg")
    assert resp.status_code == 202
    fake_pipeline.run_llm_extraction.assert_awaited_once()


# ---------- GET /kg-stats ----------


async def test_kg_stats_404_when_doc_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/documents/missing/kg-stats")
    assert resp.status_code == 404


async def test_kg_stats_returns_empty_before_extraction(
    client: TestClient, store: SQLiteStore
) -> None:
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    resp = client.get("/api/v1/documents/d1/kg-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_id"] == "d1"
    assert body["kg_status"] == "unprocessed"
    assert body["total"] == 0
    assert body["ok_count"] == 0
    assert body["total_cost_yuan"] == 0.0


async def test_kg_stats_returns_aggregates_after_logs(
    client: TestClient, store: SQLiteStore
) -> None:
    from stream_kg.kg.models import ChunkRecord

    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    await store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="c1", doc_id="d1", content="x", chunk_type="text",
                char_start=0, char_end=1, token_count=1,
            ),
        ]
    )
    await store.insert_kg_extraction_log(
        log_id="l1", chunk_id="c1", doc_id="d1", status="ok", attempt_count=1,
        elapsed_sec=1.0, prompt_tokens=100, completion_tokens=200, cost_yuan=0.0004,
        entities_count=5, relations_count=2,
    )
    await store.set_document_kg_status("d1", "ready")

    resp = client.get("/api/v1/documents/d1/kg-stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kg_status"] == "ready"
    assert body["total"] == 1
    assert body["ok_count"] == 1
    assert body["total_entities"] == 5
    assert body["total_relations"] == 2
    assert body["total_cost_yuan"] == pytest.approx(0.0004)
