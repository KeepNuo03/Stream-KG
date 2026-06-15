"""文档思维导图生成服务。"""

from __future__ import annotations

from pathlib import Path

from stream_kg.llm.deepseek_client import DeepSeekClient, DeepSeekError
from stream_kg.storage.sqlite_store import SQLiteStore

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "mindmap.txt"


class MindmapService:
    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        llm_client: DeepSeekClient | None = None,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.llm_client = llm_client or DeepSeekClient()

    async def get_or_generate(self, doc_id: str) -> dict:
        cached = await self.sqlite_store.get_document_mindmap(doc_id)
        if cached:
            return cached

        document = await self.sqlite_store.get_document(doc_id)
        if document is None:
            raise ValueError(f"Document {doc_id} not found")

        chunks = await self.sqlite_store.list_chunks_by_doc(doc_id)
        if not chunks:
            markdown = f"# {document.title}\n\n- 暂无可用内容"
        else:
            try:
                chunk_text = "\n\n".join(
                    f"[chunk {idx + 1}] {c.content[:600]}"
                    for idx, c in enumerate(chunks[:12])
                )
                template = PROMPT_PATH.read_text(encoding="utf-8")
                prompt = template.replace("{chunks}", chunk_text)
                markdown = await self.llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2048,
                    timeout_sec=90.0,
                )
                markdown = markdown.strip()
                if not markdown.startswith("#"):
                    markdown = f"# {document.title}\n\n{markdown}"
            except (DeepSeekError, Exception):
                markdown = await self._fallback_markdown(
                    title=document.title,
                    doc_id=doc_id,
                    chunks=chunks,
                )

        await self.sqlite_store.upsert_document_mindmap(doc_id=doc_id, markdown=markdown)
        saved = await self.sqlite_store.get_document_mindmap(doc_id)
        return saved or {"doc_id": doc_id, "markdown": markdown, "generated_at": ""}

    async def _fallback_markdown(
        self,
        *,
        title: str,
        doc_id: str,
        chunks: list,
    ) -> str:
        """LLM 超时/失败时，用实体 + chunk 标题拼简易大纲（保证 UI 有内容）。"""
        lines = [f"# {title}", "## 文档结构"]
        seen_sections: set[str] = set()
        for chunk in chunks[:20]:
            section = (chunk.section_title or "").strip()
            if section and section not in seen_sections:
                seen_sections.add(section)
                lines.append(f"- {section[:40]}")
        entities = await self.sqlite_store.list_entities_by_doc(doc_id)
        if entities:
            lines.append("## 关键实体")
            for ent in entities[:24]:
                name = str(ent.get("canonical_name") or ent.get("entity_id") or "")
                etype = str(ent.get("entity_type") or "concept")
                if name:
                    lines.append(f"- {name} ({etype})")
        if len(lines) <= 2:
            lines.append("- （内容较短，暂无结构化大纲）")
        return "\n".join(lines)
