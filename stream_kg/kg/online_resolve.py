"""C1：在线实体消解。"""

from __future__ import annotations

import re

from stream_kg.config import settings
from stream_kg.kg.models import EntityCandidate, EntityMention, EntityType, ResolveResult
from uuid import uuid4


_COMPATIBLE_TYPES: dict[EntityType, set[EntityType]] = {
    "concept": {"concept", "method", "metric", "dataset"},
    "method": {"method", "concept", "metric"},
    "person": {"person", "organization"},
    "dataset": {"dataset", "concept", "method"},
    "metric": {"metric", "concept", "method"},
    "organization": {"organization", "person"},
}


class OnlineResolver:
    """根据向量/词面/类型/别名分数决定 merge 或 create。"""

    def resolve(self, mention: EntityMention, candidates: list[EntityCandidate]) -> ResolveResult:
        """对单个 mention 执行消解决策。"""
        if not candidates:
            return self._create_result(mention=mention, score=0.0)

        best: tuple[float, EntityCandidate] | None = None
        for candidate in candidates:
            if not self._types_compatible(mention.entity_type, candidate.entity_type):
                continue
            composite = self._composite_score(mention=mention, candidate=candidate)
            if best is None or composite > best[0]:
                best = (composite, candidate)

        if best is None:
            return self._create_result(mention=mention, score=0.0)

        best_score, best_candidate = best
        if best_score >= settings.resolve_threshold:
            return ResolveResult(
                action="merge",
                entity_id=best_candidate.entity_id,
                canonical_name=best_candidate.canonical_name,
                entity_type=best_candidate.entity_type,
                score=best_score,
            )
        return self._create_result(mention=mention, score=best_score)

    def _create_result(self, *, mention: EntityMention, score: float) -> ResolveResult:
        return ResolveResult(
            action="create",
            entity_id=str(uuid4()),
            canonical_name=mention.surface_form,
            entity_type=mention.entity_type,
            score=score,
        )

    def _types_compatible(self, left: EntityType, right: EntityType) -> bool:
        if left == right:
            return True
        return right in _COMPATIBLE_TYPES.get(left, {left})

    def _composite_score(self, *, mention: EntityMention, candidate: EntityCandidate) -> float:
        cosine_norm = max(0.0, min(1.0, (candidate.score + 1.0) / 2.0))
        lexical = self._jaccard_lexical(mention.surface_form, candidate.canonical_name)
        type_match = 1.0 if mention.entity_type == candidate.entity_type else 0.55
        alias_bonus = self._alias_bonus(mention.surface_form, candidate)
        return (
            settings.resolve_alpha * cosine_norm
            + settings.resolve_beta * lexical
            + settings.resolve_gamma * type_match
            + 0.20 * alias_bonus
        )

    def _alias_bonus(self, surface_form: str, candidate: EntityCandidate) -> float:
        surface = surface_form.strip().lower()
        if not surface:
            return 0.0
        if surface == candidate.canonical_name.strip().lower():
            return 1.0
        for alias in candidate.aliases:
            if surface == alias.strip().lower():
                return 0.95
        return 0.0

    def _jaccard_lexical(self, left: str, right: str) -> float:
        left_tokens = self._tokenize(left)
        right_tokens = self._tokenize(right)
        if not left_tokens or not right_tokens:
            return 0.0
        inter = left_tokens & right_tokens
        union = left_tokens | right_tokens
        if not union:
            return 0.0
        return len(inter) / len(union)

    def _tokenize(self, text: str) -> set[str]:
        normalized = text.strip().lower()
        if not normalized:
            return set()
        parts = [p for p in re.split(r"[^a-z0-9\u4e00-\u9fff]+", normalized) if p]
        if parts:
            return set(parts)
        return {c for c in normalized if not c.isspace()}
