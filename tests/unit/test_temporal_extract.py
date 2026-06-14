"""Unit tests for C3 temporal relation extraction."""

from __future__ import annotations

from stream_kg.kg.models import ChunkRecord, EntityMention
from stream_kg.kg.temporal_extract import TemporalExtractor


def _resolved_mention(
    *,
    entity_id: str,
    surface: str,
    start: int,
    end: int,
    chunk_id: str = "chunk-1",
) -> EntityMention:
    return EntityMention(
        mention_id=f"m-{entity_id}",
        doc_id="doc-1",
        chunk_id=chunk_id,
        surface_form=surface,
        entity_type="method",
        char_start=start,
        char_end=end,
        context_snippet=surface,
        entity_id=entity_id,
    )


def test_detects_improves_relation_from_context() -> None:
    content = "BERT improves Transformer performance on downstream tasks."
    chunks = [
        ChunkRecord(
            chunk_id="chunk-1",
            doc_id="doc-1",
            content=content,
            chunk_type="text",
            char_start=0,
            char_end=len(content),
            token_count=12,
        )
    ]
    mentions = [
        _resolved_mention(entity_id="e-bert", surface="BERT", start=0, end=4),
        _resolved_mention(entity_id="e-transformer", surface="Transformer", start=13, end=24),
    ]
    edges = TemporalExtractor().extract(mentions, chunks)
    assert len(edges) == 1
    assert edges[0].relation_type == "improves"
    assert edges[0].confidence >= 0.6


def test_fallback_to_mentions_without_signal() -> None:
    content = "Alpha Beta"
    chunks = [
        ChunkRecord(
            chunk_id="chunk-1",
            doc_id="doc-1",
            content=content,
            chunk_type="text",
            char_start=0,
            char_end=len(content),
            token_count=2,
        )
    ]
    mentions = [
        _resolved_mention(entity_id="e-a", surface="Alpha", start=0, end=5),
        _resolved_mention(entity_id="e-b", surface="Beta", start=6, end=10),
    ]
    edges = TemporalExtractor().extract(mentions, chunks)
    assert len(edges) == 1
    assert edges[0].relation_type == "mentions"
