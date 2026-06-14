"""文本分块器。

MVP 阶段采用简化 token 窗口分块：
- 固定窗口 chunk_size；
- 固定重叠 chunk_overlap；
- 生成可追踪的 char_start/char_end。
"""

from __future__ import annotations

from uuid import uuid4

from stream_kg.kg.models import ChunkRecord


class Chunker:
    """基于 token 窗口的简易分块器。"""

    def __init__(self, *, chunk_size: int = 512, chunk_overlap: int = 64) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, *, doc_id: str, text: str, page_num: int | None = None) -> list[ChunkRecord]:
        """将文本切分为重叠 chunk。"""
        tokens = text.split()
        if not tokens:
            return []

        chunks: list[ChunkRecord] = []
        start = 0
        content_cursor = 0
        # 双指针滑窗：每轮生成一个 chunk，然后按 overlap 回退。
        while start < len(tokens):
            end = min(start + self.chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            content = " ".join(chunk_tokens).strip()
            if content:
                char_start = content_cursor
                char_end = char_start + len(content)
                chunks.append(
                    ChunkRecord(
                        chunk_id=str(uuid4()),
                        doc_id=doc_id,
                        content=content,
                        chunk_type="text",
                        page_num=page_num,
                        section_title=None,
                        char_start=char_start,
                        char_end=char_end,
                        token_count=len(chunk_tokens),
                        embedding_id=None,
                    )
                )
                content_cursor = char_end + 1
            if end >= len(tokens):
                break
            # 保证 start 单调前进，避免 overlap 过大时死循环。
            start = max(end - self.chunk_overlap, start + 1)
        return chunks
