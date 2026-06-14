"""Unit tests for entity extraction quality gates."""

from __future__ import annotations

from stream_kg.encoding.entity_extractor import EntityExtractor, is_meaningful_graph_label
from stream_kg.kg.models import ChunkRecord


def _chunk(content: str, chunk_id: str = "chunk-1") -> ChunkRecord:
    return ChunkRecord(
        chunk_id=chunk_id,
        doc_id="doc-1",
        content=content,
        chunk_type="text",
        char_start=0,
        char_end=len(content),
        token_count=10,
    )


def test_filters_english_stopword_noise() -> None:
    extractor = EntityExtractor()
    mentions = extractor.extract_from_chunk(
        _chunk("We can use this model for the task with Redis and Transformer.")
    )
    surfaces = {item.surface_form.lower() for item in mentions}
    assert "use" not in surfaces
    assert "this" not in surfaces
    assert "for" not in surfaces
    assert "the" not in surfaces
    assert "redis" in surfaces
    assert "transformer" in surfaces


def test_meaningful_graph_label_filters_generic_nouns() -> None:
    assert not is_meaningful_graph_label("design")
    assert not is_meaningful_graph_label("network")
    assert is_meaningful_graph_label("Redis")
    assert is_meaningful_graph_label("Transformer")


def test_respects_per_chunk_limit() -> None:
    extractor = EntityExtractor(per_chunk_limit=3)
    content = " ".join(f"Concept{i}" for i in range(20))
    mentions = extractor.extract_from_chunk(_chunk(content))
    assert len(mentions) <= 3
