# 03 — 系统架构

## 模块职责

| 模块 | 路径 | 职责 | 禁止 |
|------|------|------|------|
| ingestion | `stream_kg/ingestion/` | 解析、分块 | LLM、embedding |
| encoding | `stream_kg/encoding/` | 向量化、**LLM 实体抽取** | 图更新 |
| kg | `stream_kg/kg/` | C1、C3、图更新 | 检索、UI |
| retrieval | `stream_kg/retrieval/` | 检索、RAG | ingestion |
| pipeline | `stream_kg/pipeline/` | 编排 | 业务逻辑 |
| storage | `stream_kg/storage/` | SQLite、Qdrant | — |
| api | `api/` | HTTP | 核心算法 |
| frontend | `frontend/` | UI | 直连 Qdrant/LLM |

## 端口

| 服务 | 端口 |
|------|------|
| Frontend | 3000 |
| FastAPI | 8000 |
| Qdrant | 6333 |
| Embedding | 8081 |
| Reranker | 8082 |

## 存储（修正：无 graph_meta.db）

- SQLite: `data/meta.db` — 全部元数据
- Qdrant: `data/qdrant/` — 向量
- Graph: `data/graph.pkl` — NetworkX + `asyncio.Lock`

详见 [10-storage-schema.md](10-storage-schema.md)。
