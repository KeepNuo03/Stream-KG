"""Unit tests for LLM KG extraction pydantic models (P3-X · Phase A)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stream_kg.kg.llm_models import (
    MAX_ENTITIES_PER_CHUNK,
    SALIENCE_FLOOR,
    KgExtraction,
    LlmEntity,
    LlmRelation,
)


# ---------- LlmEntity 字段校验 ----------


def test_entity_strips_whitespace_in_text_fields() -> None:
    e = LlmEntity(name="  Transformer  ", type="method", description="  hi  ")
    assert e.name == "Transformer"
    assert e.description == "hi"


def test_entity_aliases_dedupe_strip_preserve_order() -> None:
    e = LlmEntity(
        name="BERT",
        type="method",
        aliases=["Bert", " BERT ", "Bert", "", "  "],
    )
    assert e.aliases == ["Bert", "BERT"]


def test_entity_invalid_type_rejected() -> None:
    with pytest.raises(ValidationError):
        LlmEntity(name="x", type="not_a_real_type")  # type: ignore[arg-type]


def test_entity_salience_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        LlmEntity(name="x", type="method", salience=1.5)
    with pytest.raises(ValidationError):
        LlmEntity(name="x", type="method", salience=-0.1)


def test_entity_empty_name_rejected() -> None:
    with pytest.raises(ValidationError):
        LlmEntity(name="", type="method")


# ---------- LlmRelation 字段校验 ----------


def test_relation_mentions_not_in_semantic_enum() -> None:
    # `mentions` 是 L0→L1 containment 边，**不应**被 LLM 抽出来。
    # llm_models.LlmRelationType 不含 mentions（13 文档 §1.4）。
    with pytest.raises(ValidationError):
        LlmRelation(head="A", relation="mentions", tail="B")  # type: ignore[arg-type]


def test_relation_invalid_relation_rejected() -> None:
    with pytest.raises(ValidationError):
        LlmRelation(head="A", relation="unknown_rel", tail="B")  # type: ignore[arg-type]


# ---------- KgExtraction 治理（13 文档 §2.5） ----------


def test_extraction_filters_below_salience_floor() -> None:
    payload = {
        "entities": [
            {"name": "X", "type": "method", "salience": 0.95},
            {"name": "Y", "type": "concept", "salience": SALIENCE_FLOOR - 0.01},
            {"name": "Z", "type": "concept", "salience": SALIENCE_FLOOR},
        ],
        "relations": [],
    }
    ext = KgExtraction.model_validate(payload)
    names = {e.name for e in ext.entities}
    assert names == {"X", "Z"}, "salience<floor 应丢；=floor 应保留"


def test_extraction_truncates_to_top_n_by_salience() -> None:
    # 15 个实体，salience 升序 0.40..0.96
    entities = [
        {"name": f"E{i}", "type": "concept", "salience": 0.40 + i * 0.04}
        for i in range(15)
    ]
    ext = KgExtraction.model_validate({"entities": entities, "relations": []})
    assert len(ext.entities) == MAX_ENTITIES_PER_CHUNK
    surviving = {e.name for e in ext.entities}
    assert "E14" in surviving, "salience 最高的 E14 必须保留"
    assert "E0" not in surviving, "salience 最低的 E0 必须截断"


def test_extraction_drops_relations_with_dangling_endpoints() -> None:
    payload = {
        "entities": [
            {"name": "Transformer", "type": "method", "salience": 0.9},
            {"name": "RNN", "type": "method", "salience": 0.6},
        ],
        "relations": [
            {"head": "Transformer", "relation": "improves", "tail": "RNN", "confidence": 0.8},
            # 端点不存在 → 丢
            {"head": "Transformer", "relation": "improves", "tail": "GPT", "confidence": 0.8},
            {"head": "CNN", "relation": "improves", "tail": "RNN", "confidence": 0.8},
        ],
    }
    ext = KgExtraction.model_validate(payload)
    assert len(ext.relations) == 1
    assert ext.relations[0].tail == "RNN"
    assert ext.relations[0].head == "Transformer"


def test_extraction_filtering_entity_cascades_to_relations() -> None:
    """salience 不达标的 entity 被砍后，依赖它的 relation 也应被丢。"""
    payload = {
        "entities": [
            {"name": "Transformer", "type": "method", "salience": 0.9},
            {"name": "RareThing", "type": "concept", "salience": 0.2},  # 砍掉
        ],
        "relations": [
            {
                "head": "Transformer",
                "relation": "uses",
                "tail": "RareThing",
                "confidence": 0.7,
            }
        ],
    }
    ext = KgExtraction.model_validate(payload)
    assert len(ext.entities) == 1
    assert ext.relations == [], "依赖被砍 entity 的 relation 必须级联丢"


def test_extraction_empty_is_valid() -> None:
    ext = KgExtraction.model_validate({"entities": [], "relations": []})
    assert ext.entities == []
    assert ext.relations == []


def test_extraction_missing_optional_fields_get_defaults() -> None:
    # LLM 可能省略 aliases / description / salience → 用默认值
    ext = KgExtraction.model_validate(
        {"entities": [{"name": "X", "type": "method"}], "relations": []}
    )
    assert ext.entities[0].aliases == []
    assert ext.entities[0].description == ""
    # 默认 salience=0.5 >= floor 所以保留
    assert ext.entities[0].salience == 0.5


def test_extraction_full_attention_paper_sample_works_end_to_end() -> None:
    """端到端：模拟 PoC 实际看到的 abstract 抽取结果，应全部通过治理。"""
    payload = {
        "entities": [
            {"name": "Transformer", "type": "method", "salience": 0.95},
            {"name": "attention mechanism", "type": "concept", "salience": 0.7},
            {"name": "machine translation", "type": "task", "salience": 0.7},
            {"name": "WMT 2014 English-to-German", "type": "dataset", "salience": 0.65,
             "aliases": ["WMT 2014"]},
            {"name": "BLEU", "type": "metric", "salience": 0.6},
            {"name": "Ashish Vaswani", "type": "person", "salience": 0.5},
            {"name": "Google Brain", "type": "organization", "salience": 0.5},
        ],
        "relations": [
            {"head": "Transformer", "relation": "uses", "tail": "attention mechanism",
             "evidence": "based solely on attention mechanisms", "confidence": 0.95},
            {"head": "Transformer", "relation": "evaluates_on", "tail": "BLEU",
             "evidence": "achieves 28.4 BLEU", "confidence": 0.9},
            {"head": "Ashish Vaswani", "relation": "affiliated_with", "tail": "Google Brain",
             "evidence": "Authors include Ashish Vaswani ...", "confidence": 0.95},
        ],
    }
    ext = KgExtraction.model_validate(payload)
    assert len(ext.entities) == 7
    assert len(ext.relations) == 3
    types = {e.type for e in ext.entities}
    assert types == {"method", "concept", "task", "dataset", "metric", "person", "organization"}
