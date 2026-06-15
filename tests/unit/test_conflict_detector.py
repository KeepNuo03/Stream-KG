"""Unit tests for conflict detector (mock LLM)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from stream_kg.kg.conflict_detector import ConflictDetector
from stream_kg.kg.graph_store import GraphStore
from stream_kg.storage.sqlite_store import SQLiteStore


@pytest.fixture
async def stores(tmp_path: Path):
    sqlite = SQLiteStore(str(tmp_path / "meta.db"))
    await sqlite.initialize()
    graph = GraphStore(str(tmp_path / "graph.pkl"))
    await graph.initialize()
    return sqlite, graph


async def test_conflict_detector_skips_when_llm_says_no(stores) -> None:
    sqlite, graph = stores
    llm = AsyncMock()
    llm.chat.return_value = '{"is_conflict": false, "confidence": 0.2, "reason": "ok"}'
    detector = ConflictDetector(sqlite_store=sqlite, graph_store=graph, llm_client=llm)
    count = await detector.scan_after_ingest(doc_id="d1")
    assert count == 0
