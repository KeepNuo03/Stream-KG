"""Chat citation payload serialization tests."""

from __future__ import annotations

from api.routes.chat import _citation_payload
from stream_kg.kg.models import Citation


def test_citation_payload_includes_entities() -> None:
    payload = _citation_payload(
        Citation(
            citation_id="1",
            doc_id="doc-1",
            chunk_id="chunk-1",
            doc_title="Doc",
            snippet="snippet",
            page_num=3,
            section_title="intro",
            entities=[
                {
                    "entity_id": "ent-1",
                    "label": "Transformer",
                    "entity_type": "concept",
                    "mention_count": 2,
                }
            ],
        )
    )
    assert payload["citation_id"] == "1"
    assert payload["entities"] == [
        {
            "entity_id": "ent-1",
            "label": "Transformer",
            "entity_type": "concept",
            "mention_count": 2,
        }
    ]
