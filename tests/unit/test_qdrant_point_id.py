"""Qdrant point id 映射单测。"""

from __future__ import annotations

import uuid

from stream_kg.storage.qdrant_point_id import (
    entity_id_to_qdrant_point_id,
    is_valid_qdrant_point_id,
)


def test_ent_hash_maps_to_deterministic_uuid() -> None:
    entity_id = "ent_4ddebbaf9f4"
    point_id = entity_id_to_qdrant_point_id(entity_id)
    assert is_valid_qdrant_point_id(point_id)
    assert entity_id_to_qdrant_point_id(entity_id) == point_id
    uuid.UUID(point_id)


def test_uuid_passthrough() -> None:
    raw = "550e8400-e29b-41d4-a716-446655440000"
    assert entity_id_to_qdrant_point_id(raw) == raw


def test_unsigned_integer_passthrough() -> None:
    assert entity_id_to_qdrant_point_id("42") == "42"
