"""Unit tests for C1 online entity resolution."""

from __future__ import annotations

from stream_kg.kg.models import EntityCandidate, EntityMention
from stream_kg.kg.online_resolve import OnlineResolver


def _mention(surface: str, entity_type: str = "method") -> EntityMention:
    return EntityMention(
        mention_id="m-1",
        doc_id="doc-1",
        chunk_id="chunk-1",
        surface_form=surface,
        entity_type=entity_type,
        char_start=0,
        char_end=len(surface),
        context_snippet=surface,
    )


def test_alias_hit_prefers_merge() -> None:
    resolver = OnlineResolver()
    candidate = EntityCandidate(
        entity_id="e-1",
        canonical_name="Redis",
        entity_type="method",
        score=0.55,
        aliases=["redis-cache"],
    )
    result = resolver.resolve(_mention("redis-cache"), [candidate])
    assert result.action == "merge"
    assert result.entity_id == "e-1"


def test_incompatible_entity_type_skips_candidate() -> None:
    resolver = OnlineResolver()
    candidate = EntityCandidate(
        entity_id="e-2",
        canonical_name="Alice",
        entity_type="person",
        score=0.99,
        aliases=[],
    )
    result = resolver.resolve(_mention("Alice", entity_type="method"), [candidate])
    assert result.action == "create"
    assert result.entity_id != "e-2"
