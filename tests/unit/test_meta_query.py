"""Unit tests for meta-query intents and direct answers (R-018)."""

from __future__ import annotations

from datetime import UTC, datetime

from stream_kg.kg.models import DocumentRecord
from stream_kg.retrieval.meta_query import (
    build_meta_answer,
    detect_meta_intent,
    is_browse_latest_doc_intent,
    is_browse_latest_url_intent,
    is_browse_resume_intent,
    resolve_browse_target,
)


def _doc(doc_id: str, title: str, *, doc_type: str = "pdf", status: str = "ready", page_count: int | None = None) -> DocumentRecord:
    return DocumentRecord(
        doc_id=doc_id,
        title=title,
        doc_type=doc_type,  # type: ignore[arg-type]
        source_uri=f"/uploads/{doc_id}.pdf",
        status=status,  # type: ignore[arg-type]
        ingested_at=datetime.now(UTC),
        page_count=page_count,
    )


# ---------- 元问题识别 ----------
def test_detect_count_intent() -> None:
    assert detect_meta_intent("一共有几篇文档") == "count"
    assert detect_meta_intent("一工有几篇文档") == "count"  # 即使有错别字也能命中「几篇文档」
    assert detect_meta_intent("文档数量") == "count"
    assert detect_meta_intent("一共有多少份资料") == "count"


def test_detect_list_intent() -> None:
    assert detect_meta_intent("我的文档") == "list"
    assert detect_meta_intent("都有哪些文档") == "list"
    assert detect_meta_intent("列一下文档") == "list"


def test_concept_query_is_not_meta() -> None:
    assert detect_meta_intent("知识图谱是什么") is None
    assert detect_meta_intent("程诺的求职目标") is None


# ---------- 直答内容 ----------
def test_count_answer_includes_total_and_ready() -> None:
    docs = [
        _doc("d1", "简历"),
        _doc("d2", "YOLO 论文"),
        _doc("d3", "处理中", status="processing"),
    ]
    answer = build_meta_answer("count", documents=docs)
    assert "3" in answer.text  # total
    assert "2" in answer.text  # ready
    assert "处理中" in answer.text


def test_list_answer_includes_titles() -> None:
    docs = [_doc("d1", "简历"), _doc("d2", "YOLO 论文")]
    answer = build_meta_answer("list", documents=docs)
    assert "简历" in answer.text
    assert "YOLO 论文" in answer.text


def test_list_answer_empty_library() -> None:
    answer = build_meta_answer("list", documents=[])
    assert "资料库为空" in answer.text


# ---------- 浏览意图识别 ----------
def test_browse_resume_intent() -> None:
    assert is_browse_resume_intent("你看一下简历") is True
    assert is_browse_resume_intent("简历") is True
    assert is_browse_resume_intent("简历里写了什么是什么") is False  # 反问句不算


def test_browse_latest_doc_intent() -> None:
    assert is_browse_latest_doc_intent("你看一下我现在导入的文档") is True
    assert is_browse_latest_doc_intent("看看刚导入") is True


def test_browse_latest_url_intent() -> None:
    assert is_browse_latest_url_intent("最新导入的链接讲了啥") is True


# ---------- 浏览目标解析 ----------
def test_resolve_resume_target_matches_title() -> None:
    docs = [_doc("d1", "YOLO 论文"), _doc("d2", "程诺-简历")]
    target = resolve_browse_target("你看一下简历", docs)
    assert target is not None
    assert target.doc_id == "d2"
    assert target.reason == "resume"


def test_resolve_resume_target_returns_none_when_no_resume_title() -> None:
    # 关键：宁可返回 None（让流程提示用户），也不能把论文当简历
    docs = [_doc("d1", "YOLO 论文"), _doc("d2", "Transformer Survey")]
    target = resolve_browse_target("你看一下简历", docs)
    assert target is None


def test_resolve_latest_doc_target_returns_first_ready() -> None:
    docs = [_doc("d1", "最近导入的论文"), _doc("d2", "更早的资料")]
    target = resolve_browse_target("看看我现在导入的文档", docs)
    assert target is not None
    assert target.doc_id == "d1"


def test_resolve_latest_url_target_prefers_web_doc() -> None:
    docs = [_doc("d1", "PDF 论文", doc_type="pdf"), _doc("d2", "知乎专栏", doc_type="web")]
    target = resolve_browse_target("最新导入的链接里有啥", docs)
    assert target is not None
    assert target.doc_id == "d2"
