"""Unit tests for SQLite schema upgrade (P3-X · Phase A.5)。

覆盖：
- documents.kg_status 字段：旧库 ALTER 兼容、默认值、CHECK 由应用层 enforce
- kg_extraction_logs 表：写入 + 汇总
- DocumentRecord.kg_status / kg_error_message 同步
- ALTER 幂等性（重复 initialize 不抛错）
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from stream_kg.storage.sqlite_store import SQLiteStore


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


async def test_initialize_creates_kg_columns_and_table(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(documents)") as cur:
            cols = {row[1]: row for row in await cur.fetchall()}
        assert "kg_status" in cols
        assert "kg_error_message" in cols

        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_extraction_logs'"
        ) as cur:
            assert (await cur.fetchone()) is not None


async def test_initialize_is_idempotent(db_path: str) -> None:
    """重复 initialize 不应抛 'duplicate column' 错（关键回归保护）。"""
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.initialize()
    await store.initialize()


async def test_alter_migrates_pre_phase_a_db(db_path: str) -> None:
    """模拟 Phase A 之前的旧库（无 kg_status 列），initialize 应通过 ALTER 升级。"""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                doc_type TEXT NOT NULL CHECK (doc_type IN ('pdf', 'web')),
                source_uri TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','processing','ready','failed')),
                published_at TEXT,
                ingested_at TEXT NOT NULL,
                page_count INTEGER,
                error_message TEXT,
                metadata_json TEXT DEFAULT '{}'
            );
            """
        )
        await db.execute(
            "INSERT INTO documents(doc_id, title, doc_type, source_uri, status, ingested_at)"
            " VALUES ('d1', 'Old Doc', 'pdf', 'file:///x.pdf', 'ready', '2024-01-01T00:00:00+00:00')"
        )
        await db.commit()

    store = SQLiteStore(db_path)
    await store.initialize()

    # 旧行应自动回填 kg_status='unprocessed'（来自 DEFAULT）
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "unprocessed"
    assert doc.kg_error_message is None


async def test_create_document_default_kg_status(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )

    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "unprocessed"

    # list_documents 路径也应填充
    docs, total = await store.list_documents()
    assert total == 1
    assert docs[0].kg_status == "unprocessed"


async def test_set_document_kg_status_full_lifecycle(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )

    # extracting
    await store.set_document_kg_status("d1", "extracting")
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "extracting"
    assert doc.kg_error_message is None

    # ready
    await store.set_document_kg_status("d1", "ready")
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "ready"

    # failed 带错误信息
    await store.set_document_kg_status(
        "d1", "failed", error_message="LLM HTTP 503"
    )
    doc = await store.get_document("d1")
    assert doc is not None
    assert doc.kg_status == "failed"
    assert doc.kg_error_message == "LLM HTTP 503"


async def test_insert_kg_extraction_log_and_stats(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    # chunks 是 kg_extraction_logs 的外键依赖，必须先建
    await store.upsert_chunks(
        [
            # ChunkRecord 字段对齐：chunk_id/doc_id/content/chunk_type/char_start/char_end/token_count
            _make_chunk("c1", "d1"),
            _make_chunk("c2", "d1"),
            _make_chunk("c3", "d1"),
        ]
    )

    await store.insert_kg_extraction_log(
        log_id="l1", chunk_id="c1", doc_id="d1", status="ok", attempt_count=1,
        elapsed_sec=2.5, prompt_tokens=100, completion_tokens=200, cost_yuan=0.001,
        entities_count=5, relations_count=3,
    )
    await store.insert_kg_extraction_log(
        log_id="l2", chunk_id="c2", doc_id="d1", status="ok", attempt_count=2,
        elapsed_sec=3.0, prompt_tokens=120, completion_tokens=180, cost_yuan=0.0011,
        entities_count=4, relations_count=2,
    )
    await store.insert_kg_extraction_log(
        log_id="l3", chunk_id="c3", doc_id="d1", status="failed", attempt_count=3,
        elapsed_sec=8.0, prompt_tokens=150, completion_tokens=0, cost_yuan=0.0001,
        entities_count=0, relations_count=0, error_message="JSON parse fail",
        raw_output="{ broken",
    )

    stats = await store.get_kg_extraction_stats(doc_id="d1")
    assert stats["total"] == 3
    assert stats["ok_count"] == 2
    assert stats["failed_count"] == 1
    assert stats["total_prompt_tokens"] == 370
    assert stats["total_completion_tokens"] == 380
    assert stats["total_entities"] == 9
    assert stats["total_relations"] == 5
    assert stats["total_cost_yuan"] == pytest.approx(0.0022, rel=1e-3)


async def test_kg_extraction_logs_cascade_on_doc_delete(db_path: str) -> None:
    """删文档应级联删 logs（外键 ON DELETE CASCADE 验证）。"""
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(
        doc_id="d1", title="t", doc_type="pdf", source_uri="file:///x.pdf"
    )
    await store.upsert_chunks([_make_chunk("c1", "d1")])
    await store.insert_kg_extraction_log(
        log_id="l1", chunk_id="c1", doc_id="d1", status="ok", attempt_count=1,
        elapsed_sec=1.0, prompt_tokens=10, completion_tokens=20, cost_yuan=0.0,
        entities_count=1, relations_count=0,
    )
    stats_before = await store.get_kg_extraction_stats(doc_id="d1")
    assert stats_before["total"] == 1

    await store.delete_document("d1")
    stats_after = await store.get_kg_extraction_stats(doc_id="d1")
    assert stats_after["total"] == 0


# ---------- helpers ----------


def _make_chunk(chunk_id: str, doc_id: str):
    from stream_kg.kg.models import ChunkRecord

    return ChunkRecord(
        chunk_id=chunk_id,
        doc_id=doc_id,
        content="dummy content",
        chunk_type="text",
        char_start=0,
        char_end=10,
        token_count=2,
    )
