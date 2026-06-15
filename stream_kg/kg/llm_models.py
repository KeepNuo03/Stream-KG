"""LLM 知识抽取的 pydantic 数据模型与类型枚举（P3-X · Phase A）。

与 `stream_kg/kg/models.py`（旧规则抽取 dataclass）**并存**：
- 旧 `EntityType`（6 种）/ `TemporalRelType`（5 种）继续给旧规则抽取代码用，避免连环改动；
- 本文件的 `LlmEntityType`（12 种）/ `LlmRelationType`（10 种）严格对齐 13 文档
  §1.3 / §1.4 taxonomy，仅给 LLM 抽取路径使用；
- 上层入图（Phase B 的 GraphStore.upsert_entity_node 等）会做 type 校验和回落。

设计点：
- 用 pydantic v2 BaseModel + Literal 类型，让 jsonschema 校验在反序列化阶段强约束；
- model_validator 兜底做 3 件治理（13 文档 §2.5 错误处理矩阵）：
  1. salience < SALIENCE_FLOOR (0.4) 的实体丢弃
  2. 实体超过 MAX_ENTITIES_PER_CHUNK (12) 按 salience 截断 top-N
  3. relation 的 head/tail 必须出现在 entities 列表，否则丢弃该 relation
"""

from __future__ import annotations

import logging
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# === 12 类实体（13 文档 §1.3） ===
LlmEntityType = Literal[
    "person",
    "organization",
    "paper",
    "method",
    "concept",
    "dataset",
    "metric",
    "task",
    "location",
    "time",
    "tool",
    "role",
]

# === 10 类语义关系（13 文档 §1.4，不含 containment 用的 mentions） ===
# `mentions` 是 L0(doc) → L1(entity) 的 containment 边，由 GraphStore.upsert_doc_entity_link
# 在写图时自动生成，不参与 LLM 抽取，所以不在此枚举。
LlmRelationType = Literal[
    "proposes",
    "improves",
    "extends",
    "contradicts",
    "part_of",
    "uses",
    "evaluates_on",
    "affiliated_with",
    "authors",
    "co_occurs",
]

# === 抽取治理常量（13 文档 §2.5） ===
# 单 chunk 实体硬上限；超过则按 salience 截断 top-N（噪声治理）
MAX_ENTITIES_PER_CHUNK = 12
# 实体 salience 下限；prompt 已要求 LLM 自行 filter，这里兜底
SALIENCE_FLOOR = 0.4


class LlmEntity(BaseModel):
    """LLM 抽出的单个实体。"""

    name: str = Field(min_length=1, max_length=200)
    type: LlmEntityType
    aliases: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=200)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("name", "description", mode="after")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("aliases", mode="after")
    @classmethod
    def _strip_aliases(cls, vs: list[str]) -> list[str]:
        # 去除空串、首尾空白；去重保序
        seen: set[str] = set()
        cleaned: list[str] = []
        for a in vs:
            t = (a or "").strip()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        return cleaned


class LlmRelation(BaseModel):
    """LLM 抽出的单条关系。"""

    head: str = Field(min_length=1, max_length=200)
    relation: LlmRelationType
    tail: str = Field(min_length=1, max_length=200)
    evidence: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("head", "tail", "evidence", mode="after")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return (v or "").strip()


# 内部缓存合法枚举集合，避免每次校验都走 get_args
_VALID_ENTITY_TYPES: frozenset[str] = frozenset(get_args(LlmEntityType))
_VALID_RELATION_TYPES: frozenset[str] = frozenset(get_args(LlmRelationType))


class KgExtraction(BaseModel):
    """单 chunk 的 LLM 抽取结果（顶层 JSON 对象）。

    反序列化时自动应用 5 道治理（见模块 docstring + 真实 smoke 后扩充）：
    1. entity type 非法 → 回落 'concept'（保留实体；smoke R-023 实测发现 LLM 偶发）
    2. relation type 非法 → **整条 relation 丢弃**（不污染图谱语义；同 R-023）
    3. salience<0.4 entity 丢
    4. entities 超 12 按 salience 截断 top-N
    5. relation 端点不在 entities 列表 → 丢

    1 & 2 在 `mode='before'` 跑（pydantic Literal 校验之前），所以 list 中**单条非法**
    不会导致整个 KgExtraction 校验失败 —— 这是 Phase A smoke 暴露的关键修复：
    PoC 没遇到这种情况，14-chunk 真跑就因为 LLM 凭空发明 `involved_in` 关系导致
    整 chunk（10+ 个 entities）丢失。

    用法：
        raw_json_str = await deepseek.chat(...)
        extraction = KgExtraction.model_validate_json(raw_json_str)
    """

    entities: list[LlmEntity] = Field(default_factory=list)
    relations: list[LlmRelation] = Field(default_factory=list)

    @field_validator("entities", mode="before")
    @classmethod
    def _coerce_unknown_entity_types(cls, v: Any) -> Any:
        """非法 entity type → 回落 'concept' 保留实体。"""
        if not isinstance(v, list):
            return v
        cleaned: list[Any] = []
        for item in v:
            if not isinstance(item, dict):
                cleaned.append(item)
                continue
            etype = item.get("type")
            if etype is not None and etype not in _VALID_ENTITY_TYPES:
                logger.info(
                    "LLM emitted unknown entity type %r for name=%r → fallback to 'concept'",
                    etype,
                    item.get("name"),
                )
                item = {**item, "type": "concept"}
            cleaned.append(item)
        return cleaned

    @field_validator("relations", mode="before")
    @classmethod
    def _drop_unknown_relations(cls, v: Any) -> Any:
        """非法 relation type → 整条 relation 丢弃（不污染图谱语义）。"""
        if not isinstance(v, list):
            return v
        cleaned: list[Any] = []
        for item in v:
            if not isinstance(item, dict):
                cleaned.append(item)
                continue
            rtype = item.get("relation")
            if rtype is not None and rtype not in _VALID_RELATION_TYPES:
                logger.info(
                    "LLM emitted unknown relation type %r for %r-?->-%r → drop relation",
                    rtype,
                    item.get("head"),
                    item.get("tail"),
                )
                continue
            cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def _enforce_extraction_policies(self) -> KgExtraction:
        # 3) 低 salience 过滤
        self.entities = [e for e in self.entities if e.salience >= SALIENCE_FLOOR]
        # 4) 超量截断（按 salience 降序）
        if len(self.entities) > MAX_ENTITIES_PER_CHUNK:
            self.entities = sorted(self.entities, key=lambda e: e.salience, reverse=True)[
                :MAX_ENTITIES_PER_CHUNK
            ]
        # 5) 关系端点必须在 entities 列表中，否则丢弃（防止 LLM 凭空捏造连接）
        valid_names = {e.name for e in self.entities}
        self.relations = [
            r for r in self.relations if r.head in valid_names and r.tail in valid_names
        ]
        return self
