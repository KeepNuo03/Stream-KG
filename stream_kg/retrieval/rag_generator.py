"""RAG 生成器。

职责：
- 将检索到的 chunk 组装成带编号上下文；
- 调用远程 LLM 生成答案；
- 解析答案中的 [n] 引用编号，回传给上游做 citation 映射。
"""

from __future__ import annotations

import re
from typing import Any
from typing import AsyncIterator

from stream_kg.config import settings
from stream_kg.encoding.text_quality import is_usable_document_text
from stream_kg.kg.models import RetrievalChunk
from stream_kg.llm import DeepSeekClient, DeepSeekError
from stream_kg.storage.sqlite_store import SQLiteStore


# 系统提示词：明确角色 + 输出格式规范（Markdown / LaTeX / 代码块 / 引用）。
# 前端用 react-markdown + remark-gfm + remark-math + rehype-katex 渲染，
# 因此 LLM 输出应使用 Markdown 而非纯文本。
RAG_SYSTEM_PROMPT = (
    "你是严谨的检索增强问答助手，回答必须基于用户提供的上下文。\n"
    "输出严格使用 Markdown 渲染（前端会自动解析），具体格式要求：\n"
    "1. 标题用 ##（不要用一级 #，避免视觉过大）；要点用无序列表 - 或有序列表 1.；强调用 **加粗**。\n"
    "2. 数学公式必须用 LaTeX：行内公式用 $...$（例如 $E = mc^2$、$d_k$），"
    "块级独立公式用 $$...$$ 并独占一行；不要写裸文本如 'QK^T / √dk'。\n"
    "3. 代码必须使用 ``` 围栏并标语言（如 ```python ... ```）；行内变量/函数名用反引号 `xxx`。\n"
    "4. 引用编号 [1] [2] 紧跟在被支撑的结论或要点末尾，不要单独成行。\n"
    "5. 优先用结构化呈现：先 1 句结论，再分点展开；必要时用表格 | a | b | 对比信息。\n"
    "6. 仅当上下文与问题明显不相关时，才回答“根据已有资料无法回答”。"
)


class RagGenerator:
    """基于检索上下文生成“可引用”答案。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        llm_client: DeepSeekClient | None = None,
    ) -> None:
        self.sqlite_store = sqlite_store
        # 复用统一的 DeepSeek 客户端（Phase A.2 重构）。
        # 测试可以注入 mock client；prod 默认从 settings 加载 key/base/model。
        self.llm_client = llm_client or DeepSeekClient()

    async def generate(self, *, query: str, chunks: list[RetrievalChunk]) -> dict[str, Any]:
        """生成答案并返回被引用的上下文索引。"""
        if not chunks:
            return {
                "answer": "根据已有资料无法回答该问题，请先导入相关文档后再试。",
                "used_indexes": [],
            }

        prompt = await self.build_prompt(query=query, chunks=chunks)

        answer = await self._call_llm(prompt)
        if chunks and self.is_refusal_answer(answer):
            answer = self.build_evidence_fallback_answer(query=query, chunks=chunks)
        answer, used_indexes = self.finalize_answer_with_citations(answer=answer, chunk_count=len(chunks))
        return {"answer": answer, "used_indexes": used_indexes}

    async def build_prompt(self, *, query: str, chunks: list[RetrievalChunk]) -> str:
        """组装可追溯的 RAG prompt。"""
        # 给每段上下文编号，确保 [1][2] 引用可稳定回溯。
        context_lines: list[str] = []
        for idx, item in enumerate(chunks, start=1):
            document = await self.sqlite_store.get_document(item.chunk.doc_id)
            title = document.title if document else item.chunk.doc_id
            page = f"p.{item.chunk.page_num}" if item.chunk.page_num else "n/a"
            # 控制单段上下文长度，避免极长 chunk 导致首 token 延迟显著升高。
            chunk_text = item.chunk.content.strip()
            max_chars = settings.rag_max_context_chars_per_chunk
            if len(chunk_text) > max_chars:
                chunk_text = f"{chunk_text[:max_chars]}\n...[truncated]"
            context_lines.append(f"[{idx}] ({title}, {page}) {chunk_text}")

        # 这里仅传 Context + User，格式与角色约束已统一在 RAG_SYSTEM_PROMPT 中下发。
        return (
            f"Context:\n{chr(10).join(context_lines)}\n\n"
            f"User: {query}"
        )

    def finalize_answer_with_citations(self, *, answer: str, chunk_count: int) -> tuple[str, list[int]]:
        """抽取引用编号，并对缺失引用做兜底修复。"""
        # 从答案中抽取 [n] 引用编号，过滤越界编号。
        used_indexes = sorted(
            {
                int(match)
                for match in re.findall(r"\[(\d+)\]", answer)
                if match.isdigit() and 1 <= int(match) <= chunk_count
            }
        )
        # 若模型未返回引用，不强行改写正文，避免“答非所问 + 硬挂引用”的体验问题。
        # 引用列表可为空，交由前端按“无引用回答”展示。
        return answer, used_indexes

    def is_refusal_answer(self, answer: str) -> bool:
        """判断回答是否属于“模板化拒答”。"""
        compact = re.sub(r"\s+", "", answer or "")
        if not compact:
            return True
        refusal_patterns = [
            "根据已有资料无法回答",
            "无法回答该问题",
            "无法基于提供的上下文回答",
            "信息不足无法回答",
            "资料不足无法回答",
        ]
        return any(pattern in compact for pattern in refusal_patterns)

    def build_evidence_fallback_answer(self, *, query: str, chunks: list[RetrievalChunk]) -> str:
        """模型拒答时，用检索证据拼装一个可读保底回答。"""
        bullet_lines: list[str] = []
        max_refs = min(3, len(chunks))
        for idx, item in enumerate(chunks[:max_refs], start=1):
            snippet = self._compact_snippet(item.chunk.content, max_chars=120)
            if not snippet:
                continue
            bullet_lines.append(f"- {snippet}[{idx}]")

        if not bullet_lines:
            return "当前已命中资料，但可用证据不足，建议补充更相关文档后再试。"

        return (
            f"基于当前检索到的资料可先得到以下要点：\n"
            + "\n".join(bullet_lines)
            + "\n\n说明：以上为证据摘录式总结，若需要更完整结论，建议补充同主题文档。"
        )

    def _compact_snippet(self, text: str, *, max_chars: int) -> str:
        """压缩片段文本，便于在保底回答中展示。"""
        normalized = re.sub(r"\s+", " ", (text or "")).strip()
        if not normalized:
            return ""
        if len(normalized) <= max_chars:
            return normalized
        return f"{normalized[:max_chars]}..."

    def is_context_text_usable(self, text: str) -> bool:
        """判断 chunk 文本是否可用于回答（过滤明显乱码）。"""
        return is_usable_document_text(text)

    def _rag_messages(self, prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": RAG_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    async def stream_llm(self, *, prompt: str) -> AsyncIterator[str]:
        """以真正的远程流式输出返回 token 文本片段。

        Phase A.2 重构：底层走 `DeepSeekClient.chat_stream()`，
        失败兜底行为完全不变（前端展示一行可读错误 + 引用提示）。
        """
        if not self.llm_client.is_ready:
            yield "当前未配置 LLM_API_KEY，已返回基于检索片段的占位结果。[1]"
            return

        try:
            async for token in self.llm_client.chat_stream(messages=self._rag_messages(prompt)):
                yield token
        except DeepSeekError as exc:
            yield (
                f"LLM 流式调用失败（{exc.__class__.__name__}），"
                f"请稍后重试。基于当前检索片段可先参考：[1]"
            )

    async def _call_llm(self, prompt: str) -> str:
        """非流式调用 LLM；失败时返回可读兜底文本。"""
        if not self.llm_client.is_ready:
            return "当前未配置 LLM_API_KEY，已返回基于检索片段的占位结果。[1]"

        try:
            return await self.llm_client.chat(messages=self._rag_messages(prompt))
        except DeepSeekError as exc:
            # MVP 阶段不中断主流程，返回可读错误文本便于前端展示。
            return (
                f"LLM 调用失败（{exc.__class__.__name__}），"
                f"请稍后重试。基于当前检索片段可先参考：[1]"
            )
