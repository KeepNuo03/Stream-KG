# 05 — 核心算法

## C1 — Online Entity Resolution

**候选检索**：Qdrant `entities` collection ANN search（`top_k=20`），不自建 HNSW。

```
FOR each mention m (with m.vector from embed(surface_form | context)):
  candidates = qdrant.search_entities(m.vector, top_k=20)
  FOR each candidate c:
    score = α·cos_sim + β·jaccard_lexical + γ·type_match
  IF best.score >= τ_merge → merge ELSE create
```

| 参数 | 默认 |
|------|------|
| α | 0.60 |
| β | 0.25 |
| γ | 0.15 |
| τ_merge | 0.82 |

## C3 — Temporal Relation Extraction

- 触发：C1 后，新文档与已有实体共现的对
- LLM 输出 JSON，`confidence >= 0.7` 才入库
- `contradicts` 边 UI 红色

## Query Router（P3）

含 `对比|区别|演变|关系|改进|矛盾|冲突|compare|...` → `hybrid`，否则 `vector`。

P1-P2：`FEATURE_GRAPH_ROUTER_ENABLED=false`，恒为 `vector`。

## RAG 生成

| Phase | 送入 LLM 的 chunks |
|-------|-------------------|
| P1-P2 | Top `RETRIEVAL_TOP_K`（10），无 rerank |
| P3 | Rerank 后 Top `RERANK_TOP_K`（5） |

Hybrid 模式追加 entity 2-hop 邻居 chunks。

Prompt 要求标注 `[1][2]` 引用；不足则明确拒答。
