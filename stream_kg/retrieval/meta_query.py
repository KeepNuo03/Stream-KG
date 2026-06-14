"""元问题与浏览意图识别 + 直答。

设计目标：
- 「库里有几篇 / 都有什么文档 / 列一下」等问题 **直接查 SQLite**，不走 RAG；
- 「看一下简历 / 我刚导入的链接」等浏览意图，**精确锁定一份目标文档**，避免论文 chunk 混入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from stream_kg.kg.models import ChunkRecord, DocumentRecord, RetrievalChunk

MetaIntent = Literal["count", "list", "latest"]


@dataclass(slots=True)
class MetaAnswer:
    """直接回答的元问题，不走 LLM。"""

    text: str
    intent: MetaIntent
    referenced_doc_ids: list[str]


@dataclass(slots=True)
class BrowseTarget:
    """浏览意图锁定的单一文档。"""

    doc_id: str
    title: str
    reason: str  # resume / latest / latest_web / explicit


_COUNT_PATTERNS = (
    "几篇",
    "几份",
    "几个文档",
    "几个文件",
    "几条",
    "多少篇",
    "多少份",
    "多少个文档",
    "多少文档",
    "多少条文档",
    "文档数量",
    "文件数量",
    "几篇文档",
    "几篇文章",
    "总数",
    "总共有",
    "一共有",
    "共有多少",
)

_LIST_PATTERNS = (
    "都有什么文档",
    "有哪些文档",
    "都有哪些文档",
    "有什么文档",
    "列一下文档",
    "列出文档",
    "列出所有文档",
    "文档列表",
    "我的文档",
    "导入了什么",
    "导入了哪些",
    "都导入了",
    "现在有什么",
    "库里有什么",
    "库里有哪些",
    "资料库里有",
)

_BROWSE_RESUME_PATTERNS = (
    "简历",
    "resume",
    "cv",
)

_BROWSE_LATEST_DOC_PATTERNS = (
    "看一下文档",
    "看看文档",
    "看一下我的文档",
    "看看我的文档",
    "看一下我现在导入",
    "看看我现在导入",
    "看一下导入的文档",
    "看看导入的文档",
    "看一下当前文档",
    "看看当前文档",
    "看一下刚导入",
    "看看刚导入",
    "我刚导入的文档",
    "我现在导入的文档",
    "当前导入的文档",
)

_BROWSE_LATEST_URL_PATTERNS = (
    "最新链接",
    "最新传入的链接",
    "最新导入的链接",
    "刚导入的链接",
    "刚导入的网页",
    "这个链接里有啥",
    "链接里有啥",
    "url里有啥",
    "url内容",
)


def _compact(query: str) -> str:
    return re.sub(r"\s+", "", (query or ""))


def _compact_lower(query: str) -> str:
    return _compact(query).lower()


def detect_meta_intent(query: str) -> MetaIntent | None:
    compact = _compact(query)
    lower = compact.lower()
    if any(p in compact for p in _COUNT_PATTERNS):
        return "count"
    if any(p in compact for p in _LIST_PATTERNS):
        return "list"
    if "list documents" in lower or "how many docs" in lower or "how many documents" in lower:
        return "count" if "many" in lower else "list"
    return None


def is_browse_resume_intent(query: str) -> bool:
    compact = _compact(query)
    lower = compact.lower()
    if not any(p in compact or p in lower for p in _BROWSE_RESUME_PATTERNS):
        return False
    # 必须是「看 / 总结 / 介绍」类意图，而不是「简历是什么」
    triggers = ("看", "总结", "概括", "介绍", "说说", "讲讲", "读一下", "读读", "show", "summarize")
    return any(t in compact or t in lower for t in triggers) or compact in {"简历", "resume", "cv"}


def is_browse_latest_doc_intent(query: str) -> bool:
    compact = _compact(query)
    return any(p in compact for p in _BROWSE_LATEST_DOC_PATTERNS)


def is_browse_latest_url_intent(query: str) -> bool:
    compact_lower = _compact_lower(query)
    return any(p in compact_lower for p in _BROWSE_LATEST_URL_PATTERNS)


def is_any_browse_intent(query: str) -> bool:
    return (
        is_browse_resume_intent(query)
        or is_browse_latest_doc_intent(query)
        or is_browse_latest_url_intent(query)
    )


def build_meta_answer(
    intent: MetaIntent,
    *,
    documents: list[DocumentRecord],
) -> MetaAnswer:
    ready_docs = [doc for doc in documents if doc.status == "ready"]
    failed_docs = [doc for doc in documents if doc.status == "failed"]
    processing_docs = [doc for doc in documents if doc.status in {"pending", "processing"}]
    total = len(documents)
    ready_count = len(ready_docs)

    if intent == "count":
        lines = [
            f"资料库当前共有 **{total}** 份文档，其中已就绪 **{ready_count}** 份。",
        ]
        if processing_docs:
            lines.append(f"另有 {len(processing_docs)} 份正在处理中。")
        if failed_docs:
            lines.append(f"{len(failed_docs)} 份处理失败，可在文档区重试或删除。")
        if ready_docs:
            sample = "、".join(f"《{doc.title}》" for doc in ready_docs[:5])
            more = "等" if ready_count > 5 else ""
            lines.append(f"已就绪文档示例：{sample}{more}。")
        text = "\n".join(lines)
        return MetaAnswer(text=text, intent=intent, referenced_doc_ids=[d.doc_id for d in ready_docs[:5]])

    # list
    if total == 0:
        return MetaAnswer(
            text="资料库为空，尚未导入任何文档。请先在左侧文档区上传 PDF 或添加 URL。",
            intent=intent,
            referenced_doc_ids=[],
        )

    lines = [f"资料库共有 **{total}** 份文档（已就绪 {ready_count} 份）："]
    for idx, doc in enumerate(ready_docs[:10], start=1):
        kind = "PDF" if doc.doc_type == "pdf" else "网页"
        page = f"，{doc.page_count} 页" if doc.page_count else ""
        lines.append(f"{idx}. [{kind}] 《{doc.title}》{page}")
    if ready_count > 10:
        lines.append(f"…还有 {ready_count - 10} 份未列出。")
    if processing_docs:
        lines.append(f"处理中：{len(processing_docs)} 份。")
    if failed_docs:
        lines.append(f"失败：{len(failed_docs)} 份，可在文档区查看原因。")
    return MetaAnswer(text="\n".join(lines), intent=intent, referenced_doc_ids=[d.doc_id for d in ready_docs[:10]])


def resolve_browse_target(
    query: str,
    documents: list[DocumentRecord],
) -> BrowseTarget | None:
    """根据浏览意图，从已就绪文档中挑出 **唯一** 目标。"""
    ready_docs = [doc for doc in documents if doc.status == "ready"]
    if not ready_docs:
        return None
    compact = _compact(query)
    lower = compact.lower()

    if is_browse_resume_intent(query):
        for doc in ready_docs:
            title = doc.title or ""
            if "简历" in title or any(k in title.lower() for k in ("resume", "cv")):
                return BrowseTarget(doc_id=doc.doc_id, title=title, reason="resume")
        # 没有标题匹配 → 不要硬塞最新文档，避免混入论文
        return None

    if is_browse_latest_url_intent(query):
        for doc in ready_docs:
            if doc.doc_type == "web":
                return BrowseTarget(doc_id=doc.doc_id, title=doc.title or doc.source_uri, reason="latest_web")
        return None

    if is_browse_latest_doc_intent(query):
        latest = ready_docs[0]
        return BrowseTarget(doc_id=latest.doc_id, title=latest.title, reason="latest")

    # 标题精确匹配：例如「看一下《X》」「X 这篇」
    for doc in ready_docs:
        title = doc.title or ""
        if not title or len(title) < 3:
            continue
        if title in compact or title.lower() in lower:
            return BrowseTarget(doc_id=doc.doc_id, title=title, reason="explicit")
    return None


_RESUME_KEYWORD_GROUPS: tuple[tuple[str, ...], ...] = (
    ("个人简历", "求职意向", "求职目标", "意向岗位", "应聘岗位", "应届"),
    ("教育经历", "教育背景", "学历", "本科", "硕士", "研究生", "GPA", "gpa"),
    ("工作经历", "实习经历", "项目经历", "实习", "项目经验", "校园经历"),
    ("掌握技能", "技术栈", "专业技能", "语言能力", "技能清单", "技能特长"),
    ("@", "邮箱", "phone", "电话", "tel:", "联系电话"),
)


def looks_like_resume_text(text: str | None) -> bool:
    """启发式判断一段文本是否像简历内容。

    用 5 组「教育/求职/工作/技能/联系方式」关键词覆盖；命中两组以上即视为简历。
    用途：当文档标题不含「简历/resume/cv」时，靠首段 chunk 把简历兜出来。
    """
    if not text:
        return False
    sample = text[:1200].lower()
    matched_groups = 0
    for group in _RESUME_KEYWORD_GROUPS:
        if any(kw.lower() in sample for kw in group):
            matched_groups += 1
            if matched_groups >= 2:
                return True
    return False


def resolve_browse_target_with_samples(
    query: str,
    documents: list[DocumentRecord],
    *,
    head_text_by_doc: dict[str, str] | None = None,
) -> BrowseTarget | None:
    """加强版 `resolve_browse_target`：标题匹配失败后用 chunk 文本启发式兜底。

    `head_text_by_doc` 形如 `{doc_id: 首段chunk文本}`，可选；
    当意图为「浏览简历」且标题不含简历相关词时，会按该映射逐份判断。
    """
    primary = resolve_browse_target(query, documents)
    if primary is not None:
        return primary

    if not is_browse_resume_intent(query) or not head_text_by_doc:
        return None

    ready_docs = [doc for doc in documents if doc.status == "ready"]
    for doc in ready_docs:
        sample = head_text_by_doc.get(doc.doc_id)
        if looks_like_resume_text(sample):
            return BrowseTarget(doc_id=doc.doc_id, title=doc.title or "简历", reason="resume_content")
    return None


def build_locked_chunks(
    chunks: Iterable[ChunkRecord],
    *,
    is_usable: Any,
) -> list[RetrievalChunk]:
    """把锁定文档的 chunk 包成 RetrievalChunk，分数按顺序衰减（保证排序稳定）。"""
    locked: list[RetrievalChunk] = []
    for index, chunk in enumerate(chunks):
        if not is_usable(chunk.content):
            continue
        locked.append(RetrievalChunk(chunk=chunk, score=max(0.0, 1.0 - index * 0.001)))
    return locked
