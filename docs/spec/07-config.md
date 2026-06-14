# 07 — 配置

完整模板见项目根目录 [`.env.example`](../../.env.example)。

## 关键变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_PROVIDER` | deepseek | 已决议 |
| `LLM_MODEL` | deepseek-chat | DeepSeek-V3 |
| `EMBEDDING_DIM` | 1024 | Qwen3-Embedding-0.6B |
| `INGEST_POLL_INTERVAL_SEC` | 2 | 前端轮询间隔 |
| `FEATURE_KG_ENABLED` | false | P2 开启 |

## Feature Flags

见 [01-phases.md](01-phases.md)。
