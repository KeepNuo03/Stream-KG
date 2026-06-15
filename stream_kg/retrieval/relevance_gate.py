"""检索质量门控：向量分 + 词面重叠，避免低相关 chunk 进入生成或证据保底。"""

from __future__ import annotations

import re

from stream_kg.kg.models import RetrievalChunk

_TERM_STOPWORDS = frozenset(
    {
        "什么",
        "如何",
        "怎么",
        "为什么",
        "哪些",
        "可否",
        "是否",
        "能否",
        "一下",
        "东西",
        "内容",
        "资料",
        "文档",
        "问题",
        "介绍",
        "解释",
        "说明",
        "the",
        "what",
        "how",
        "why",
        "when",
        "where",
        "which",
        "this",
        "that",
        "with",
        "from",
        "about",
    }
)

_BROAD_DOC_PATTERNS = (
    "讲了什么",
    "讲了啥",
    "说的什么",
    "主要内容",
    "总结一下",
    "概括",
    "概述",
    "大意",
    "核心内容",
    "重点是什么",
    "这篇",
    "这个文档",
    "这篇文章",
)


def normalize_query_for_terms(query: str) -> str:
    text = (query or "").strip()
    text = re.sub(
        r"(是什么|什么是|是啥|有哪些|怎么样|如何|怎么|为什么|吗|呢)$",
        "",
        text,
    )
    return text.strip()


def _expand_chinese_term(token: str) -> set[str]:
    """长短语拆子词，避免「程诺的求职目标」整句无法命中只含「程诺」的 chunk。"""
    out: set[str] = set()
    if len(token) < 2 or token in _TERM_STOPWORDS:
        return out
    if not re.search(r"[\u4e00-\u9fff]", token):
        return out
    out.add(token)
    if len(token) >= 4:
        out.add(token[:2])
        out.add(token[-2:])
        if len(token) >= 6:
            out.add(token[2:4])
    return out


def extract_query_terms(query: str) -> set[str]:
    normalized = normalize_query_for_terms(query)
    if not normalized:
        return set()

    terms: set[str] = set()
    parts = re.split(r"[，,。！？、；;\s]+", normalized)
    if not parts:
        parts = [normalized]

    for part in parts:
        if not part:
            continue
        segments = re.split(r"的", part) if part else []
        if not segments:
            segments = [part]
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            if re.search(r"[\u4e00-\u9fff]", segment):
                terms.update(_expand_chinese_term(segment))
            for match in re.finditer(r"\b[A-Za-z]{3,}\b", segment):
                word = match.group().lower()
                if word not in _TERM_STOPWORDS:
                    terms.add(word)

    return terms


def is_broad_document_query(query: str) -> bool:
    compact = re.sub(r"\s+", "", query or "")
    return any(pattern in compact for pattern in _BROAD_DOC_PATTERNS)


def lexical_hit_count(query: str, texts: list[str]) -> int:
    terms = extract_query_terms(query)
    if not terms:
        return 1
    combined = " ".join(texts).lower()
    return sum(1 for term in terms if term.lower() in combined)


def lexical_overlap_ratio(query: str, texts: list[str]) -> float:
    terms = extract_query_terms(query)
    if not terms:
        return 1.0
    hits = lexical_hit_count(query, texts)
    return hits / len(terms)


def assess_retrieval(
    query: str,
    chunks: list[RetrievalChunk],
    *,
    min_score: float,
    low_margin: float,
    min_lexical_overlap: float = 0.2,
) -> dict[str, object]:
    """判断检索结果是否足以支撑回答。"""
    if not chunks:
        return {
            "acceptable": False,
            "low_confidence": True,
            "reason": "empty",
            "top_score": None,
            "lexical_overlap": 0.0,
        }

    top_score = float(chunks[0].score)
    second_score = float(chunks[1].score) if len(chunks) > 1 else 0.0
    top_texts = [item.chunk.content for item in chunks[:3]]
    overlap = lexical_overlap_ratio(query, top_texts)
    hits = lexical_hit_count(query, top_texts)
    broad = is_broad_document_query(query)
    terms = extract_query_terms(query)
    # 泛化「看看文档/简历」类问题向量分往往偏低，单独放宽阈值
    effective_min = min_score if not broad else max(0.18, min_score * 0.5)

    if top_score < effective_min:
        return {
            "acceptable": False,
            "low_confidence": True,
            "reason": "low_vector_score",
            "top_score": top_score,
            "lexical_overlap": overlap,
        }

    # 非泛化文档问题时：top chunk 中至少出现一个查询词（人名/术语），避免误拒「程诺求职」类问题
    if not broad and terms and hits == 0:
        return {
            "acceptable": False,
            "low_confidence": True,
            "reason": "no_lexical_overlap",
            "top_score": top_score,
            "lexical_overlap": overlap,
        }

    # R-021：跨语言/术语场景下，词法 overlap 天生偏低（如中文 query 问英文论文：
    # "自注意力" 不会出现在英文 chunk 里，但 "transformer" 命中 1 个，overlap=1/6=0.17）。
    # 当向量分明显强时，信任语义相关性，跳过严格 overlap 检查，避免词法 gate 错杀。
    strong_vector_signal = top_score >= max(0.45, effective_min + 0.10)
    if (
        not broad
        and terms
        and overlap < min_lexical_overlap
        and hits < 2
        and not strong_vector_signal
    ):
        return {
            "acceptable": False,
            "low_confidence": True,
            "reason": "no_lexical_overlap",
            "top_score": top_score,
            "lexical_overlap": overlap,
        }

    low_confidence = top_score < min_score + low_margin or (top_score - second_score) < 0.015
    return {
        "acceptable": True,
        "low_confidence": low_confidence,
        "reason": "ok",
        "top_score": top_score,
        "lexical_overlap": overlap,
    }


def should_use_evidence_fallback(
    query: str,
    chunks: list[RetrievalChunk],
    *,
    fallback_min_score: float,
) -> bool:
    """仅在检索质量足够、模型过度拒答时才用证据摘录保底。

    更严格的规则（R-017d）：
    - 必须有 ≥1 个查询词命中 top chunk，避免把简历当「知识图谱」答案；
    - 必须高于 `fallback_min_score`；
    - 泛化文档问题（讲了什么/总结一下）不走兜底，让模型自己处理。
    """
    if not chunks:
        return False
    if is_broad_document_query(query):
        return False
    assessment = assess_retrieval(
        query,
        chunks,
        min_score=fallback_min_score,
        low_margin=0.0,
        min_lexical_overlap=0.34,
    )
    if not assessment["acceptable"]:
        return False
    top_score = assessment["top_score"]
    if top_score is None or float(top_score) < fallback_min_score:
        return False
    terms = extract_query_terms(query)
    hits = lexical_hit_count(query, [c.chunk.content for c in chunks[:3]])
    if terms and hits == 0:
        return False
    return True
