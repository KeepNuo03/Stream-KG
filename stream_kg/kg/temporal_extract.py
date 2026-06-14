"""C3：时序/语义关系边抽取（R-020 重写）。

升级点：
- 同一对实体的多次共现，confidence 用 log(count+1) 累加上限封顶，让"高频共现"边
  在导出器门控里更容易过线；
- 同一对实体可能产出多种 relation_type（例如 transformer ↔ attention 既共现又
  extends），按 confidence 取胜者；
- 增加更多模板：proposes / introduces / uses / leverages。
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from stream_kg.encoding.entity_extractor import is_technical_entity_label
from stream_kg.kg.models import ChunkRecord, EntityMention, TemporalEdge, TemporalRelType, new_edge_id

_RELATION_PATTERNS: list[tuple[TemporalRelType, list[re.Pattern[str]], float]] = [
    (
        "improves",
        [
            re.compile(r"\bimprov", re.I),
            re.compile(r"\boutperform", re.I),
            re.compile(r"\benhanc", re.I),
            re.compile(r"\bbetter than\b", re.I),
            re.compile(r"\bsuperior\b", re.I),
            re.compile(r"\bsurpass", re.I),
            re.compile(r"改进|提升|优于|更好|超越|超过"),
        ],
        0.82,
    ),
    (
        "contradicts",
        [
            re.compile(r"\bcontradict", re.I),
            re.compile(r"\bhowever\b", re.I),
            re.compile(r"\bunlike\b", re.I),
            re.compile(r"\bin contrast\b", re.I),
            re.compile(r"相反|矛盾|不同于|然而"),
        ],
        0.80,
    ),
    (
        "extends",
        [
            re.compile(r"\bextend", re.I),
            re.compile(r"\bbased on\b", re.I),
            re.compile(r"\bbuild(?:s)? on\b", re.I),
            re.compile(r"\bbuilt on\b", re.I),
            re.compile(r"\bfollow(?:s)?\b", re.I),
            re.compile(r"\binspired by\b", re.I),
            re.compile(r"\bderiv(?:e|ed|ing)\b", re.I),
            re.compile(r"扩展|基于|继承|延续|衍生|启发"),
        ],
        0.78,
    ),
    (
        "surveys",
        [
            re.compile(r"\bsurvey\b", re.I),
            re.compile(r"\breview\b", re.I),
            re.compile(r"\boverview\b", re.I),
            re.compile(r"\bliterature\b", re.I),
            re.compile(r"综述|调研|回顾|概述"),
        ],
        0.76,
    ),
    (
        "extends",  # proposes / introduces 也归到 extends（最接近的语义）
        [
            re.compile(r"\bpropose(?:s|d)?\b", re.I),
            re.compile(r"\bintroduce(?:s|d)?\b", re.I),
            re.compile(r"\bpresent(?:s|ed)?\b", re.I),
            re.compile(r"提出|引入|提供"),
        ],
        0.74,
    ),
]

_MIN_RELATION_CONFIDENCE = 0.55
_MENTIONS_BASE_CONFIDENCE = 0.55  # 单次共现的初始 confidence
_MENTIONS_BOOST_CAP = 0.92  # 多次共现累加上限，避免压爆


class TemporalExtractor:
    """从已消解 mentions 提取关系边（R-020 重写：共现累加 + 模板加分）。"""

    def extract(
        self,
        mentions: list[EntityMention],
        chunks: list[ChunkRecord] | None = None,
    ) -> list[TemporalEdge]:
        """按 chunk 聚合，累计 (head, tail, relation) 的共现次数，输出加权边。"""
        chunk_text: dict[str, str] = {}
        if chunks:
            chunk_text = {chunk.chunk_id: chunk.content or "" for chunk in chunks}

        grouped: dict[str, list[EntityMention]] = {}
        for mention in mentions:
            if not mention.entity_id:
                continue
            grouped.setdefault(mention.chunk_id, []).append(mention)

        # (head_id, tail_id, rel) → {"count": n, "evidence_chunks": [...], "max_conf": float}
        accum: dict[
            tuple[str, str, TemporalRelType],
            dict[str, object],
        ] = {}
        # 用于无序 pair 去重（head/tail 顺序无关），只对 mentions 类型做对称归一
        pair_canonical: dict[tuple[str, str, TemporalRelType], tuple[str, str, TemporalRelType]] = {}

        max_pairs_per_chunk = 30  # 防止 chunk 内实体过多导致 O(n^2) 爆炸
        for chunk_id, chunk_mentions in grouped.items():
            # 同 chunk 内的实体 dedup（按 entity_id），保留首个 mention 用于上下文
            uniq: dict[str, EntityMention] = {}
            for m in sorted(chunk_mentions, key=lambda item: item.char_start):
                if m.entity_id and m.entity_id not in uniq:
                    uniq[m.entity_id] = m
            ordered = list(uniq.values())
            content = chunk_text.get(chunk_id, "")
            pair_count_in_chunk = 0

            # 在 chunk 内对所有实体对做共现（不只是相邻 pair）
            for i, left in enumerate(ordered):
                for right in ordered[i + 1 : i + 6]:  # 限制窗口避免爆炸
                    if pair_count_in_chunk >= max_pairs_per_chunk:
                        break
                    if not left.entity_id or not right.entity_id:
                        continue
                    if left.entity_id == right.entity_id:
                        continue

                    relation_type, base_conf = self._infer_relation(
                        content=content,
                        left=left,
                        right=right,
                    )

                    # mentions 类型：要求至少一端是技术词（避免泛词噪声）
                    if relation_type == "mentions" and not (
                        is_technical_entity_label(left.surface_form)
                        or is_technical_entity_label(right.surface_form)
                    ):
                        continue

                    # mentions 关系是对称的，按 entity_id 字典序归一，避免重复累计
                    if relation_type == "mentions":
                        h, t = sorted([left.entity_id, right.entity_id])
                    else:
                        h, t = left.entity_id, right.entity_id

                    key = (h, t, relation_type)
                    entry = accum.setdefault(
                        key,
                        {"count": 0, "evidence_chunks": [], "max_conf": 0.0},
                    )
                    entry["count"] = int(entry["count"]) + 1  # type: ignore[arg-type]
                    chunks_seen: list[str] = entry["evidence_chunks"]  # type: ignore[assignment]
                    if chunk_id not in chunks_seen:
                        chunks_seen.append(chunk_id)
                    entry["max_conf"] = max(float(entry["max_conf"]), float(base_conf))  # type: ignore[arg-type]
                    pair_canonical[key] = key
                    pair_count_in_chunk += 1

        # 最终把累积数据转成 edges
        edges: list[TemporalEdge] = []
        for (head_id, tail_id, rel), entry in accum.items():
            count = int(entry["count"])  # type: ignore[arg-type]
            evidence_chunks: list[str] = entry["evidence_chunks"]  # type: ignore[assignment]
            base = float(entry["max_conf"])  # type: ignore[arg-type]
            confidence = self._aggregate_confidence(
                relation_type=rel,
                base_confidence=base,
                count=count,
                cross_chunk=len(evidence_chunks) > 1,
            )
            if confidence < _MIN_RELATION_CONFIDENCE:
                continue
            edges.append(
                TemporalEdge(
                    edge_id=new_edge_id(),
                    head_entity_id=head_id,
                    tail_entity_id=tail_id,
                    relation_type=rel,
                    confidence=round(confidence, 3),
                    evidence_chunk_id=evidence_chunks[0] if evidence_chunks else "",
                    created_at=datetime.now(UTC),
                )
            )
        return edges

    def _aggregate_confidence(
        self,
        *,
        relation_type: TemporalRelType,
        base_confidence: float,
        count: int,
        cross_chunk: bool,
    ) -> float:
        """共现次数 / 跨 chunk 一致性 加权 confidence。

        mentions：从 0.55 起步，每次共现按 log 提升，跨 chunk +0.05，封顶 0.92；
        semantic（improves/extends/...）：模板基础 confidence 起步，共现/跨 chunk 锦上添花。
        """
        if relation_type == "mentions":
            boost = math.log(count + 1) * 0.12
            base = max(base_confidence, _MENTIONS_BASE_CONFIDENCE)
            value = min(base + boost, _MENTIONS_BOOST_CAP)
            if cross_chunk:
                value = min(value + 0.05, _MENTIONS_BOOST_CAP)
            return value
        # semantic：base 起步，多次出现稍微 +
        boost = math.log(count + 1) * 0.04
        value = min(base_confidence + boost, 0.96)
        if cross_chunk:
            value = min(value + 0.02, 0.96)
        return value

    def _infer_relation(
        self,
        *,
        content: str,
        left: EntityMention,
        right: EntityMention,
    ) -> tuple[TemporalRelType, float]:
        window = self._relation_window(content=content, left=left, right=right)
        best_type: TemporalRelType = "mentions"
        best_conf = 0.60

        for relation_type, patterns, base_conf in _RELATION_PATTERNS:
            if any(pattern.search(window) for pattern in patterns):
                if base_conf > best_conf:
                    best_type = relation_type
                    best_conf = base_conf

        return best_type, best_conf

    def _relation_window(self, *, content: str, left: EntityMention, right: EntityMention) -> str:
        if content and left.char_end <= right.char_start:
            between = content[left.char_end : right.char_start]
            if between.strip():
                return between
        return f"{left.context_snippet} {right.context_snippet}".strip()
