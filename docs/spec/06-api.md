# 06 — API 契约

**Base**: `http://localhost:8000/api/v1`

## 错误格式

```json
{"error": {"code": "DOCUMENT_NOT_FOUND", "message": "...", "details": {}}}
```

## Documents

| Method | Path | 说明 |
|--------|------|------|
| POST | `/documents/upload` | 202, multipart PDF |
| POST | `/documents/import-url` | 202, JSON `{url, title?}` |
| GET | `/documents` | 列表，`?status&limit&offset` |
| GET | `/documents/{doc_id}` | 详情 + **轮询 status** |
| GET | `/documents/{doc_id}/file` | **P3** 原始 PDF 流 |
| DELETE | `/documents/{doc_id}` | 204，单文档删除（默认级联清理存储与图谱关联） |
| POST | `/documents/batch-delete` | 200，批量删除（部分失败可返回详情） |
| POST | `/documents/{doc_id}/reprocess` | 202, processing 中返回 409 |

### 删除接口契约（文档先行）

#### 1) 单个删除

`DELETE /documents/{doc_id}`

- 行为：删除文档及其关联 chunk、向量数据、解析产物。
- 约束：若后期 `FEATURE_KG_ENABLED=true`，必须同时清理该文档关联的 mention/evidence/图谱节点与边（见 [10-storage-schema.md](10-storage-schema.md#4-删除级联规则)）。
- 返回：
  - `204`：成功
  - `404`：文档不存在
  - `409`：文档正在 processing（删除保护）

#### 2) 批量删除

`POST /documents/batch-delete`

请求体：

```json
{
  "doc_ids": ["uuid-1", "uuid-2", "uuid-3"]
}
```

响应体（`200`）：

```json
{
  "requested": 3,
  "deleted": 2,
  "failed": [
    {"doc_id": "uuid-3", "code": "DOCUMENT_PROCESSING", "message": "Document is currently processing"}
  ]
}
```

- 语义：**逐个执行单删除语义**，允许部分成功（便于前端批量操作）。
- 幂等：已删除或不存在的 doc_id 计入 `failed`（`DOCUMENT_NOT_FOUND`）。
- 安全：服务端仍需做逐条权限与状态校验（当前单用户模型下仅做状态校验）。

## Graph

| Method | Path | 说明 |
|--------|------|------|
| GET | `/graph` | P1 空图+placeholder；P2+ 完整图 |
| GET | `/graph/entities/{entity_id}` | 实体详情 |

## Chat

| Method | Path | 说明 |
|--------|------|------|
| POST | `/chat/sessions` | 201 `{session_id}` |
| POST | `/chat/sessions/{id}/messages` | SSE 流式 |
| GET | `/chat/sessions/{id}/messages` | 历史 |

### SSE Events

`retrieval` | `token` | `citation` | `done` | `error`

## System

| Method | Path |
|--------|------|
| GET | `/health` |
| GET | `/stats` |

## 错误码

| Code | HTTP |
|------|------|
| VALIDATION_ERROR | 400 |
| DOCUMENT_NOT_FOUND | 404 |
| ENTITY_NOT_FOUND | 404 |
| SESSION_NOT_FOUND | 404 |
| DOCUMENT_PROCESSING | 409 |
| BATCH_DELETE_PARTIAL_FAILED | 200 |
| FILE_TOO_LARGE | 413 |
| UNSUPPORTED_FILE_TYPE | 415 |
| PARSE_FAILED | 422 |
| EMBEDDING_FAILED | 500 |
| GPU_OOM | 500 |
| QDRANT_ERROR | 500 |
| LLM_API_ERROR | 502 |

完整 JSON schema 见 `api/schemas.py`。
