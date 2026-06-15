r"""Phase A 端到端 smoke：直接调 pipeline.run_llm_extraction 验证真实 DeepSeek 链路。

用途：
- 单测全 mock，跑通后还需要确认真 LLM 调用 + pydantic 反序列化 + 治理 + 写 logs 整条链没坑。
- 走 import 调用而不是起 uvicorn + curl，是因为 extract-kg 不需要 embedder/qdrant 起服务，
  少起一堆 server 来 smoke。HTTP 层 8 个单测已覆盖路由分支。

跑法：
    & "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/smoke_llm_extract.py [DOC_ID]
默认 DOC_ID = 论文 "attention is all you need"（14 chunks，按 concurrency=5 估算 20-30s）。
按 PoC 实测：14 chunks ~= CNY 0.03。
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from stream_kg.config import settings
from stream_kg.encoding.embedder import Embedder
from stream_kg.kg.graph_store import GraphStore
from stream_kg.pipeline.ingest_pipeline import IngestPipeline
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore


DEFAULT_DOC_ID = "f743c47a-68e0-46ee-9688-3378cbd571e4"  # attention is all you need


async def main(doc_id: str) -> int:
    sqlite_store = SQLiteStore(settings.sqlite_path)
    await sqlite_store.initialize()
    # run_llm_extraction 实际不调用 graph/qdrant/embedder，但 IngestPipeline.__init__
    # 需要这些对象。构造但不联网，所以 server 起不起没关系。
    pipeline = IngestPipeline(
        sqlite_store=sqlite_store,
        graph_store=GraphStore(settings.graph_path),
        qdrant_store=QdrantStore(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            chunks_collection=settings.qdrant_collection_chunks,
            entities_collection=settings.qdrant_collection_entities,
            vector_size=settings.embedding_dim,
        ),
        embedder=Embedder(
            server_url=settings.embedding_server_url,
            dim=settings.embedding_dim,
            timeout_sec=5.0,
            connect_timeout_sec=1.0,
            fallback_cooldown_sec=5,
            batch_size=8,
        ),
    )

    doc = await sqlite_store.get_document(doc_id)
    if doc is None:
        print(f"[ERR] doc {doc_id} not found in sqlite")
        return 2
    chunks = await sqlite_store.list_chunks_by_doc(doc_id)
    print(f"[INFO] doc='{doc.title}' status={doc.status} chunks={len(chunks)} kg_status={doc.kg_status}")
    print(f"[INFO] feature_kg_use_llm={settings.feature_kg_use_llm} (smoke 不看这个开关，run_llm_extraction 强制走 LLM)")
    print(f"[INFO] llm_api_base={settings.llm_api_base} model={settings.llm_model}")

    print(f"\n>>> calling pipeline.run_llm_extraction(doc_id={doc_id})...")
    summary = await pipeline.run_llm_extraction(doc_id=doc_id)
    print(f"\n<<< summary: {summary}")

    stats = await sqlite_store.get_kg_extraction_stats(doc_id=doc_id)
    print(f"\n<<< kg-stats: {stats}")

    doc_after = await sqlite_store.get_document(doc_id)
    print(f"\n<<< doc.kg_status={doc_after.kg_status} kg_error={doc_after.kg_error_message}")

    return 0 if summary.get("failed", 0) == 0 else 1


async def reset(doc_id: str) -> None:
    """重置 doc.kg_status 为 unprocessed 并删该 doc 已有 logs，便于干净重跑。"""
    import aiosqlite

    async with aiosqlite.connect(settings.sqlite_path) as db:
        await db.execute(
            "UPDATE documents SET kg_status='unprocessed', kg_error_message=NULL WHERE doc_id=?",
            (doc_id,),
        )
        await db.execute("DELETE FROM kg_extraction_logs WHERE doc_id=?", (doc_id,))
        await db.commit()
    print(f"[INFO] reset doc={doc_id}: kg_status='unprocessed', cleared logs")


if __name__ == "__main__":
    args = sys.argv[1:]
    do_reset = "--reset" in args
    args = [a for a in args if a != "--reset"]
    target = args[0] if args else DEFAULT_DOC_ID

    if do_reset:
        asyncio.run(reset(target))
    sys.exit(asyncio.run(main(target)))
