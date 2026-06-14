"""文本可用性判断（入库与 RAG 共用）。"""

from __future__ import annotations

import string


def is_usable_document_text(text: str) -> bool:
    """判断文本是否适合入库检索（过滤乱码/占位页/极短噪声）。"""
    content = (text or "").strip()
    if not content:
        return False
    if len(content) < 30:
        return False
    lowered = content.lower()
    placeholder_keywords = (
        "please wait",
        "enable javascript",
        "just a moment",
        "security check",
        "loading...",
        "访问受限",
        "安全验证",
    )
    if any(keyword in lowered for keyword in placeholder_keywords):
        return False

    sample = content[:4000]
    length = max(len(sample), 1)
    replacement_ratio = sample.count("\ufffd") / length
    if replacement_ratio > 0.05:
        return False

    readable = 0
    allowed_punct = set("，。！？；：、（）《》【】“”‘’.,!?;:()[]{}<>-_/%")
    for ch in sample:
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") or ch.isspace() or ch in allowed_punct:
            readable += 1
    if (readable / length) < 0.55:
        return False

    noisy = sum(
        1
        for ch in sample
        if ch not in string.printable and not ("\u4e00" <= ch <= "\u9fff") and not ch.isspace()
    )
    return (noisy / length) < 0.25
