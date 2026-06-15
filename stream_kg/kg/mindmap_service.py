"""文档思维导图生成服务。"""

from __future__ import annotations

from pathlib import Path

from stream_kg.llm.deepseek_client import DeepSeekClient
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
            chunk_text = "\n\n".join(
                f"[chunk {idx + 1}] {c.content[:800]}"
                for idx, c in enumerate(chunks[:24])
            )
            template = PROMPT_PATH.read_text(encoding="utf-8")
            prompt = template.replace("{chunks}", chunk_text)
            markdown = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            markdown = markdown.strip()
            if not markdown.startswith("#"):
                markdown = f"# {document.title}\n\n{markdown}"

        await self.sqlite_store.upsert_document_mindmap(doc_id=doc_id, markdown=markdown)
        saved = await self.sqlite_store.get_document_mindmap(doc_id)
        return saved or {"doc_id": doc_id, "markdown": markdown, "generated_at": ""}
