# 09 — 部署与 MinerU

## 开发环境启动顺序

```powershell
docker compose up -d qdrant
uv run python -m stream_kg.models.embedding_server   # :8081
uv run uvicorn api.main:app --reload --port 8000
cd frontend && pnpm dev                                 # :3000
# P3: uv run python -m stream_kg.models.reranker_server  # :8082
```

## MinerU 集成

| 项目 | 决策 |
|------|------|
| 调用方式 | **subprocess CLI**：`mineru -p {input} -o {output_dir}` |
| 设备 | `MINERU_DEVICE=cuda` |
| 输出 | 读取 `{output_dir}/{stem}/content.md` |
| 失败 | `status=failed`, `error_code=PARSE_FAILED`，**不 fallback Docling** |
| 串行 | 与 Embedding **不同时**占 GPU |

### Windows 注意

- 确保 `mineru` 在 PATH 中（`uv pip install mineru` 或 conda）
- `MINERU_CLI` 环境变量可覆盖命令名
- 路径使用 `pathlib.Path`，避免硬编码反斜杠

### 依赖安装

```powershell
uv sync --extra gpu --extra mineru
```

## RTX 3060 显存

| 组件 | VRAM |
|------|------|
| MinerU | ~2GB |
| Embedding 0.6B | ~1.5GB |
| Reranker 0.6B（P3 按需） | ~1.5GB |

**LLM 必须走 DeepSeek API**，不在本地部署。

## 性能基线

| 操作 | 目标 |
|------|------|
| PDF 30页入库 | ≤ 60s |
| 网页导入 | ≤ 10s |
| 向量检索 | ≤ 200ms |
| LLM 500 tokens | ≤ 5s |
