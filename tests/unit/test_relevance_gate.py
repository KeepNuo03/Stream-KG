"""Unit tests for retrieval relevance gating (R-017 regressions)."""

from __future__ import annotations

from stream_kg.kg.models import ChunkRecord, RetrievalChunk
from stream_kg.retrieval.relevance_gate import (
    assess_retrieval,
    extract_query_terms,
    is_broad_document_query,
    lexical_hit_count,
    should_use_evidence_fallback,
)


def _chunk(content: str, chunk_id: str = "c1", score: float = 0.55) -> RetrievalChunk:
    return RetrievalChunk(
        chunk=ChunkRecord(
            chunk_id=chunk_id,
            doc_id="doc-1",
            content=content,
            chunk_type="text",
            char_start=0,
            char_end=len(content),
            token_count=10,
        ),
        score=score,
    )


# ---------- R-017 主路：概念题应被拒答 ----------
def test_concept_query_rejected_when_no_lexical_overlap() -> None:
    chunks = [
        _chunk("程诺，男，22岁，求职 AI 应用开发。", "c1"),
        _chunk("dilated convolution feature extraction inference.", "c2"),
    ]
    result = assess_retrieval("知识图谱是什么", chunks, min_score=0.38, low_margin=0.04)
    assert result["acceptable"] is False
    assert result["reason"] == "no_lexical_overlap"


def test_concept_query_rejected_when_vector_score_too_low() -> None:
    chunks = [_chunk("无关内容", "c1", score=0.20)]
    result = assess_retrieval("知识图谱是什么", chunks, min_score=0.38, low_margin=0.04)
    assert result["acceptable"] is False
    assert result["reason"] == "low_vector_score"


# ---------- R-017b：实体题（人名/术语命中）应放行 ----------
def test_entity_query_accepted_with_name_hit() -> None:
    chunks = [
        _chunk("程诺，男，22岁，目标职位：AI 应用开发、大模型算法。", "c1"),
    ]
    result = assess_retrieval("程诺的求职目标是什么", chunks, min_score=0.38, low_margin=0.04)
    assert result["acceptable"] is True


def test_entity_query_extracts_subterms() -> None:
    terms = extract_query_terms("程诺的求职目标是什么")
    assert "程诺" in terms
    assert any("求职" in t for t in terms)


def test_relevant_concept_query_accepted_when_chunks_match() -> None:
    chunks = [_chunk("知识图谱用于表示实体与关系，支持推理与检索。", "c1", score=0.62)]
    result = assess_retrieval("知识图谱是什么", chunks, min_score=0.38, low_margin=0.04)
    assert result["acceptable"] is True


# ---------- R-017c：「看一下简历」已被 meta_query 处理，gate 不再特殊放宽 ----------
def test_browse_resume_is_not_treated_as_broad_doc_query() -> None:
    # 浏览意图由 meta_query 接管，gate 不再把「简历」视为 broad
    assert is_broad_document_query("你看一下简历") is False


def test_lexical_hit_count_zero_for_unrelated_query() -> None:
    hits = lexical_hit_count("知识图谱是什么", ["程诺求职简历"])
    assert hits == 0


# ---------- R-017d：证据兜底严格化 ----------
def test_evidence_fallback_skipped_when_no_lexical_hit() -> None:
    chunks = [_chunk("程诺简历内容", "c1", score=0.55)]
    assert should_use_evidence_fallback("知识图谱是什么", chunks, fallback_min_score=0.48) is False


def test_evidence_fallback_skipped_for_broad_doc_query() -> None:
    chunks = [_chunk("某文档的高相关内容", "c1", score=0.80)]
    assert should_use_evidence_fallback("这篇文章讲了什么", chunks, fallback_min_score=0.48) is False


def test_evidence_fallback_allowed_when_query_terms_hit_strongly() -> None:
    chunks = [_chunk("程诺目标岗位：AI 应用开发、大模型算法。", "c1", score=0.62)]
    assert should_use_evidence_fallback("程诺的目标岗位是什么", chunks, fallback_min_score=0.48) is True
