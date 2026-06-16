"""文档列表批量统计 SQL 单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stream_kg.kg.models import ChunkRecord, EntityMention
from stream_kg.storage.sqlite_store import SQLiteStore


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_count_chunks_by_docs_batch(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(doc_id="d1", title="A", doc_type="pdf", source_uri="/a.pdf")
    await store.create_document(doc_id="d2", title="B", doc_type="pdf", source_uri="/b.pdf")
    await store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                content="hello world content",
                chunk_type="text",
                char_start=0,
                char_end=20,
                token_count=5,
                page_num=1,
            ),
            ChunkRecord(
                chunk_id="c2",
                doc_id="d1",
                content="second chunk",
                chunk_type="text",
                char_start=21,
                char_end=40,
                token_count=4,
                page_num=2,
            ),
        ]
    )
    chunk_map = await store.count_chunks_by_docs(["d1", "d2"])
    assert chunk_map == {"d1": 2}
    assert chunk_map.get("d2", 0) == 0


@pytest.mark.asyncio
async def test_filter_entities_without_mentions(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(doc_id="d1", title="A", doc_type="pdf", source_uri="/a.pdf")
    await store.upsert_entity(entity_id="e1", canonical_name="Lonely", entity_type="concept")
    await store.upsert_entity(entity_id="e2", canonical_name="Linked", entity_type="concept")
    await store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                content="mentions linked",
                chunk_type="text",
                char_start=0,
                char_end=15,
                token_count=3,
                page_num=1,
            ),
        ]
    )
    await store.insert_entity_mention(
        EntityMention(
            mention_id="m1",
            doc_id="d1",
            chunk_id="c1",
            surface_form="Linked",
            entity_type="concept",
            char_start=0,
            char_end=6,
            context_snippet="mentions linked",
            entity_id="e2",
        )
    )
    orphans = await store.filter_entities_without_mentions(["e1", "e2"])
    assert orphans == ["e1"]


@pytest.mark.asyncio
async def test_list_chunk_entities_by_ids(db_path: str) -> None:
    store = SQLiteStore(db_path)
    await store.initialize()
    await store.create_document(doc_id="d1", title="A", doc_type="pdf", source_uri="/a.pdf")
    await store.upsert_entity(entity_id="e1", canonical_name="Transformer", entity_type="concept")
    await store.upsert_entity(entity_id="e2", canonical_name="Attention", entity_type="concept")
    await store.upsert_chunks(
        [
            ChunkRecord(
                chunk_id="c1",
                doc_id="d1",
                content="Transformer uses attention",
                chunk_type="text",
                char_start=0,
                char_end=28,
                token_count=6,
                page_num=1,
            ),
        ]
    )
    await store.insert_entity_mention(
        EntityMention(
            mention_id="m1",
            doc_id="d1",
            chunk_id="c1",
            surface_form="Transformer",
            entity_type="concept",
            char_start=0,
            char_end=10,
            context_snippet="Transformer uses attention",
            entity_id="e1",
        )
    )
    await store.insert_entity_mention(
        EntityMention(
            mention_id="m2",
            doc_id="d1",
            chunk_id="c1",
            surface_form="Attention",
            entity_type="concept",
            char_start=16,
            char_end=25,
            context_snippet="Transformer uses attention",
            entity_id="e2",
        )
    )
    # e1 再出现一次，验证 mention_count 聚合与排序。
    await store.insert_entity_mention(
        EntityMention(
            mention_id="m3",
            doc_id="d1",
            chunk_id="c1",
            surface_form="Transformer",
            entity_type="concept",
            char_start=0,
            char_end=10,
            context_snippet="Transformer uses attention",
            entity_id="e1",
        )
    )

    entity_map = await store.list_chunk_entities_by_ids(["c1"])
    assert "c1" in entity_map
    assert entity_map["c1"][0]["entity_id"] == "e1"
    assert entity_map["c1"][0]["label"] == "Transformer"
    assert entity_map["c1"][0]["mention_count"] == 2
