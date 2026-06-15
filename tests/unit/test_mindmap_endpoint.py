"""Unit tests for mindmap service cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stream_kg.kg.mindmap_service import MindmapService
from stream_kg.kg.models import ChunkRecord
from stream_kg.storage.sqlite_store import SQLiteStore


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteStore:
    s = SQLiteStore(str(tmp_path / "meta.db"))
    await s.initialize()
    return s


async def test_mindmap_cache_hit(store: SQLiteStore) -> None:
    await store.create_document(doc_id="d1", title="Doc", doc_type="pdf", source_uri="file:///a.pdf")
    await store.upsert_document_mindmap(doc_id="d1", markdown="# Doc\n\n- A")
    llm = AsyncMock()
    service = MindmapService(sqlite_store=store, llm_client=llm)
    result = await service.get_or_generate("d1")
    assert "Doc" in result["markdown"]
    llm.chat.assert_not_awaited()


async def test_mindmap_generate_and_cache(store: SQLiteStore) -> None:
    await store.create_document(doc_id="d2", title="Doc2", doc_type="pdf", source_uri="file:///b.pdf")
    await store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d2",
                content="Transformer architecture overview",
                chunk_type="text",
                char_start=0,
                char_end=20,
                token_count=5,
            )
        ]
    )
    llm = AsyncMock()
    llm.chat.return_value = "# Doc2\n\n- Transformer"
    service = MindmapService(sqlite_store=store, llm_client=llm)
    result = await service.get_or_generate("d2")
    assert "Transformer" in result["markdown"]
    cached = await store.get_document_mindmap("d2")
    assert cached is not None
