# 04 — 数据模型

## 枚举

```python
DocType = Literal["pdf", "web"]
DocStatus = Literal["pending", "processing", "ready", "failed"]
ChunkType = Literal["text", "heading", "table", "formula"]
EntityType = Literal["concept", "method", "person", "dataset", "metric", "organization"]
ResolveAction = Literal["merge", "create"]
TemporalRelType = Literal["improves", "contradicts", "extends", "surveys", "mentions"]
RetrievalMode = Literal["vector", "graph", "hybrid"]
MessageRole = Literal["user", "assistant"]
```

## EntityMention（运行时扩展）

持久化字段见 [10-storage-schema.md](10-storage-schema.md)。

| 字段 | 类型 | 持久化 | 说明 |
|------|------|--------|------|
| `mention_id` | UUID | Y | 主键 |
| `entity_id` | UUID | Y | 消解后关联实体 |
| `chunk_id` | UUID | Y | 来源 chunk |
| `doc_id` | UUID | Y | 来源文档 |
| `surface_form` | str | Y | 原文形式 |
| `entity_type` | EntityType | 运行时 | NER 预测类型 |
| `char_start/end` | int | Y | chunk 内偏移 |
| `context_snippet` | str | 运行时 | 前后各 100 字符 |
| `vector` | list[float] | **否** | 消解前 embed，见 [11-pipelines.md](11-pipelines.md) |

## 其他模型

完整字段定义见原规格；实现使用 `stream_kg/kg/models.py` + `api/schemas.py`。

| 模型 | 说明 |
|------|------|
| Document | 文档元数据 |
| Chunk | 文本块 |
| Entity | 规范实体 |
| TemporalEdge | 时序关系边 |
| ResolveResult | C1 消解结果 |
| Citation | 对话引用 |
| ChatMessage | 会话消息 |

## 文档状态机

```
pending → processing → ready | failed
failed/ready → processing (reprocess)
```

## P1 图谱 API 行为

`FEATURE_KG_ENABLED=false` 时：

```json
{
  "nodes": [],
  "edges": [],
  "stats": {"node_count": 0, "edge_count": 0},
  "placeholder": true
}
```
