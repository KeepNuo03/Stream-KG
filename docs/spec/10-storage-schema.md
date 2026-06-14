# 10 — 存储 Schema

> **P0 补丁**：SQLite DDL、Qdrant Payload、删除级联、图锁

> **实现状态（P2）**：`entities` / `entity_mentions` / `temporal_edges` 已在 `stream_kg/storage/sqlite_store.py` 落地；入图双写见 `graph_update.py`；文档删除级联见 `kg_cleanup.py`（同步 graph.pkl + 孤立实体向量清理）。

## 1. SQLite（`data/meta.db`）

### 1.1 `documents`

```sql
CREATE TABLE documents (
    doc_id          TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    doc_type        TEXT NOT NULL CHECK (doc_type IN ('pdf', 'web')),
    source_uri      TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
    published_at    TEXT,
    ingested_at     TEXT NOT NULL,
    page_count      INTEGER,
    error_message   TEXT,
    metadata_json   TEXT DEFAULT '{}'
);
CREATE INDEX idx_documents_status ON documents(status);
```

### 1.2 `chunks`

```sql
CREATE TABLE chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    chunk_type      TEXT NOT NULL,
    page_num        INTEGER,
    section_title   TEXT,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL,
    token_count     INTEGER NOT NULL,
    embedding_id    TEXT
);
CREATE INDEX idx_chunks_doc_id ON chunks(doc_id);
```

### 1.3 `entities`

```sql
CREATE TABLE entities (
    entity_id       TEXT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    aliases_json    TEXT NOT NULL DEFAULT '[]',
    description     TEXT,
    embedding_id    TEXT,
    first_seen_at   TEXT NOT NULL,
    last_updated_at TEXT NOT NULL
);
```

### 1.4 `entity_mentions`

```sql
CREATE TABLE entity_mentions (
    mention_id      TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    chunk_id        TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    surface_form    TEXT NOT NULL,
    char_start      INTEGER NOT NULL,
    char_end        INTEGER NOT NULL
);
CREATE INDEX idx_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX idx_mentions_doc ON entity_mentions(doc_id);
```

### 1.5 `temporal_edges`

```sql
CREATE TABLE temporal_edges (
    edge_id             TEXT PRIMARY KEY,
    head_entity_id      TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    tail_entity_id      TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    evidence_chunk_id   TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    confidence          REAL NOT NULL,
    valid_from          TEXT,
    created_at          TEXT NOT NULL,
    UNIQUE(head_entity_id, tail_entity_id, relation_type)
);
```

### 1.6 `chat_sessions` / `chat_messages`

```sql
CREATE TABLE chat_sessions (
    session_id      TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL
);

CREATE TABLE chat_messages (
    message_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    citations_json  TEXT,
    retrieval_mode  TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_messages_session ON chat_messages(session_id);
```

## 2. Qdrant Payload

### 2.1 Collection `chunks`

- **Vector dim**: 1024（`EMBEDDING_DIM`）
- **Distance**: Cosine
- **Point ID**: 等于 `chunk_id`（UUID 字符串）

```json
{
  "chunk_id": "uuid",
  "doc_id": "uuid",
  "page_num": 3,
  "section_title": "3. Model Architecture",
  "content_preview": "前200字..."
}
```

### 2.2 Collection `entities`

- **Point ID**: 等于 `entity_id`
- **用途**: C1 候选检索（Qdrant ANN search，不自建 HNSW）

```json
{
  "entity_id": "uuid",
  "canonical_name": "Transformer",
  "entity_type": "method",
  "aliases": ["Attention Mechanism"]
}
```

### 2.3 Collection `mention_vectors`（消解专用，可选 P2）

临时 mention 向量，消解完成后可删除或保留审计。

```json
{
  "mention_id": "uuid",
  "doc_id": "uuid",
  "surface_form": "attention mechanism"
}
```

## 3. NetworkX 图（`data/graph.pkl`）

- 节点：`entity_id`，属性 `{label, type, doc_count}`
- 边：`edge_id`，属性 `{relation_type, confidence, evidence_chunk_id}`
- **并发**：全局 `asyncio.Lock`，所有读写串行化（单进程 MVP）

## 4. 删除级联规则

`DELETE /documents/{doc_id}` 执行顺序：

```
1. 删除该 doc 所有 chunks 的 Qdrant points（collection: chunks）
2. 删除 SQLite chunks（CASCADE → entity_mentions）
3. 对每个 entity：
   a. 移除 source_chunk_ids 中属于该 doc 的 chunk
   b. 若 entity 无任何 mention 剩余 → 删除 entity + Qdrant point + 图中节点
4. 删除 evidence_chunk_id 属于该 doc 的 temporal_edges
5. 删除 documents 行
6. 删除 data/uploads/{doc_id}.pdf 及 data/parsed/{doc_id}/
7. 持久化 graph.pkl
```

### 4.1 批量删除（文档先行规范）

`POST /documents/batch-delete` 必须复用“单删除语义”，按 `doc_id` 串行或受控并发执行，保证每条文档删除都满足 4 节级联规则。

返回结构：

```json
{
  "requested": 3,
  "deleted": 2,
  "failed": [
    {"doc_id": "uuid-3", "code": "DOCUMENT_PROCESSING", "message": "..."}
  ]
}
```

约束：

1. **部分成功允许**：任一文档失败不回滚已成功删除项；
2. **结果可追踪**：`failed[]` 必须逐条包含 `doc_id/code/message`；
3. **图谱一致性（P2+）**：若开启图谱，单条删除失败不得影响其他 doc 的图谱清理；
4. **锁顺序稳定**：涉及 `graph.pkl` 更新时必须遵守统一锁顺序，避免批量删除死锁。

### 4.2 知识图谱关联删除（P2+ 强制）

当 `FEATURE_KG_ENABLED=true` 时，删除文档必须满足以下不变量：

- 不保留指向已删除 `chunk_id` 的 `entity_mentions` 与 `temporal_edges`；
- 图中不允许存在无来源 evidence 的边；
- 实体若无任何 mention 留存，应删除实体记录、向量点与图节点；
- 删除结束后 `graph.pkl` 与 SQLite/Qdrant 状态一致（同一事务语义窗口内完成）。

## 5. 文件存储

| 路径 | 内容 |
|------|------|
| `data/uploads/{doc_id}.pdf` | 原始 PDF |
| `data/parsed/{doc_id}/content.md` | MinerU 输出 |
| `data/graph.pkl` | NetworkX 序列化 |
