"""实体候选检索（Qdrant entities collection）。"""

from __future__ import annotations

from stream_kg.kg.models import EntityCandidate, EntityType
from stream_kg.storage.qdrant_store import QdrantStore


class CandidateRetrieval:
    """封装实体候选检索。"""

    def __init__(self, *, qdrant_store: QdrantStore) -> None:
        self.qdrant_store = qdrant_store

    def search(self, mention_vector: list[float], top_k: int) -> list[EntityCandidate]:
        """按向量相似度检索实体候选。"""
        if not mention_vector:
            return []
        rows = self.qdrant_store.search_entities(query_vector=mention_vector, top_k=top_k)
        candidates: list[EntityCandidate] = []
        for row in rows:
            payload = row.get("payload", {})
            canonical_name = str(payload.get("canonical_name") or "")
            entity_type = self._coerce_entity_type(str(payload.get("entity_type") or "concept"))
            aliases = payload.get("aliases") or []
            if not canonical_name:
                continue
            candidates.append(
                EntityCandidate(
                    entity_id=str(row.get("entity_id") or row.get("id") or ""),
                    canonical_name=canonical_name,
                    entity_type=entity_type,
                    score=float(row.get("score") or 0.0),
                    aliases=aliases if isinstance(aliases, list) else [],
                )
            )
        return [item for item in candidates if item.entity_id]

    def _coerce_entity_type(self, raw: str) -> EntityType:
        allowed: set[str] = {"concept", "method", "person", "dataset", "metric", "organization"}
        return raw if raw in allowed else "concept"
