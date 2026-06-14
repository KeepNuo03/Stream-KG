# 11 — 流水线规格

> **P0 补丁**：异步任务、Ingest/QA 伪代码、轮询协议

> **实现状态（P2）**：`IngestPipeline._run_incremental_kg` 已接入 C1 抽取/消解、C3 多关系抽取（`improves/contradicts/extends/surveys` + `mentions` 兜底），并双写 SQLite + graph.pkl + Qdrant entities。

## 1. 异步任务机制

| 项目 | 决策 |
|------|------|
| 调度器 | FastAPI `BackgroundTasks` |
| 状态存储 | SQLite `documents.status` |
| 前端感知 | 轮询 `GET /documents/{doc_id}`，间隔 `INGEST_POLL_INTERVAL_SEC`（默认 2s） |
| 互斥 | 同一 `doc_id` 处于 `processing` 时，拒绝 `reprocess`（409 `DOCUMENT_PROCESSING`） |
| 失败 | 设置 `status=failed`，写入 `error_message`，不自动重试 |

### 状态流转

```
pending → processing → ready | failed
failed  → processing (reprocess)
ready   → processing (reprocess)
```

## 2. IngestPipeline（P1 基础版）

```python
async def run_ingest(doc_id: str) -> None:
  try:
    await db.set_status(doc_id, "processing")

    # Step 1: Parse
    if doc_type == "pdf":
      markdown = await asyncio.to_thread(parse_pdf_mineru, upload_path)  # GPU 串行
    else:
      markdown = await asyncio.to_thread(parse_web_trafilatura, url)

    # Step 2: Chunk
    chunks = chunker.split(markdown, doc_id)

    # Step 3: Embed (local GPU server)
    vectors = await embedder.embed_batch([c.content for c in chunks])
    await qdrant.upsert_chunks(chunks, vectors)

    # Step 4: KG (P2 only, gated by FEATURE_KG_ENABLED)
    if settings.feature_kg_enabled:
      mentions = await entity_extractor.extract(chunks)
      for mention in mentions:
        mention.vector = await embedder.embed_one(
          f"{mention.surface_form} | {mention.context_snippet}"
        )
        candidates = await qdrant.search_entities(mention.vector, top_k=20)
        result = online_resolve(mention, candidates, config)
        await graph_update.apply(result, mention)
      temporal_edges = await temporal_extract.extract(new_entity_pairs, chunks)
      await graph_update.add_edges(temporal_edges)
      await graph_store.persist()

    await db.set_status(doc_id, "ready")
  except Exception as e:
    await db.set_status(doc_id, "failed", error_message=str(e))
    raise
```

## 3. Mention Embedding（C1 前置）

消解前为每个 mention 计算向量：

```python
mention.vector = embed(
  text=f"{mention.surface_form} | {context_snippet}",
  # context_snippet = 所在 chunk 前后各 100 字符
)
```

| 字段 | 说明 |
|------|------|
| `mention.vector` | 运行时计算，**不持久化到 SQLite**（可选写入 Qdrant `mention_vectors`） |
| `Entity.embedding` | 规范名+描述，持久化到 Qdrant `entities` |

## 4. QAPipeline

```python
async def run_qa(session_id: str, query: str, mode: RetrievalMode | None) -> AsyncIterator[SSEEvent]:
  mode = mode or query_router.route(query)  # P3 前恒为 vector

  chunks = await vector_search(query, top_k=settings.retrieval_top_k)

  if mode == "hybrid" and settings.feature_kg_enabled:
    entity_ids = graph_search.entities_from_query(query)
    extra = await graph_search.collect_chunks(entity_ids, hop=settings.graph_hop)
    chunks = merge_dedupe(chunks, extra)

  if settings.feature_reranker_enabled:
    chunks = await reranker.rerank(query, chunks, top_k=settings.rerank_top_k)
  else:
    chunks = chunks[:settings.retrieval_top_k]  # P1-P2: 无 rerank，直接取 top_k

  async for event in rag_generator.stream(query, chunks):
    yield event
```

### Phase 门控（RAG Top-K）

| Phase | 检索 | Top-K 送入 LLM |
|-------|------|----------------|
| P1-P2 | 向量 only | `RETRIEVAL_TOP_K`（10） |
| P3 | 向量+图+rerank | `RERANK_TOP_K`（5） |

## 5. MinerU 集成

见 [09-deployment.md](09-deployment.md#mineru)。

## 6. SSE 事件

| event | data 字段 |
|-------|-----------|
| `retrieval` | `{mode, chunk_count, entity_count}` |
| `token` | `{content}` |
| `citation` | `Citation` 对象 |
| `done` | `{message_id, citations[], retrieval_mode}` |
| `error` | `{code, message}` — LLM 中途失败 |

## 7. SSRF 防护（URL 导入）

```python
def validate_import_url(url: str) -> None:
  # 仅 http/https
  # 拒绝 localhost, 127.0.0.1, 10.x, 172.16-31.x, 192.168.x
  # 拒绝 file://
```
