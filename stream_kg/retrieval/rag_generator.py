"""RAG 生成器。

职责：
- 将检索到的 chunk 组装成带编号上下文；
- 调用远程 LLM 生成答案；
- 解析答案中的 [n] 引用编号，回传给上游做 citation 映射。
"""

from __future__ import annotations

import json
import re
from typing import Any
from typing import AsyncIterator

import httpx

from stream_kg.config import settings
from stream_kg.encoding.text_quality import is_usable_document_text
from stream_kg.kg.models import RetrievalChunk
from stream_kg.storage.sqlite_store import SQLiteStore


class RagGenerator:
    """基于检索上下文生成“可引用”答案。"""

    def __init__(self, *, sqlite_store: SQLiteStore) -> None:
        self.sqlite_store = sqlite_store

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

        return (
            "你是学习助手。请优先基于提供的上下文给出有帮助的结论，"
            "并在关键结论后标注引用编号（如[1][2]）。"
            "只有在上下文与问题明显不相关时，才回答“根据已有资料无法回答”。"
            "输出格式要求：先给1句结论，再分点列出（每点单独换行）。\n\n"
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

    async def stream_llm(self, *, prompt: str) -> AsyncIterator[str]:
        """以真正的远程流式输出返回 token 文本片段。"""
        if not settings.llm_api_key:
            yield "当前未配置 LLM_API_KEY，已返回基于检索片段的占位结果。[1]"
            return

        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": "你是严谨的检索增强问答助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": settings.llm_max_tokens,
            "temperature": settings.llm_temperature,
            "stream": True,
            # DeepSeek V4 默认可能先输出 reasoning_content；关闭后可更快输出最终答案 token。
            "thinking": {"type": "disabled"},
        }

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{settings.llm_api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                break
                            parsed = json.loads(data)
                            token = (
                                parsed.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                            )
                            if isinstance(token, str) and token:
                                yield token
        except Exception as exc:
            # 不中断主流程：把错误文本作为单条 token 输出，前端可直接展示。
            yield f"LLM 流式调用失败（{exc.__class__.__name__}），请稍后重试。基于当前检索片段可先参考：[1]"

    async def _call_llm(self, prompt: str) -> str:
        """调用远程 LLM；失败时返回可读兜底文本。"""
        if not settings.llm_api_key:
            return "当前未配置 LLM_API_KEY，已返回基于检索片段的占位结果。[1]"

        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": "你是严谨的检索增强问答助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": settings.llm_max_tokens,
            "temperature": settings.llm_temperature,
            # 与流式路径保持一致：关闭 thinking，减少首包等待并统一输出风格。
            "thinking": {"type": "disabled"},
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{settings.llm_api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "根据已有资料无法回答。[1]")
            )
        except Exception as exc:
            # MVP 阶段不中断主流程，返回可读错误文本便于前端展示。
            return f"LLM 调用失败（{exc.__class__.__name__}），请稍后重试。基于当前检索片段可先参考：[1]"
