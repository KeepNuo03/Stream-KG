# stream-kg

增量式个人知识库 MVP — 上传 PDF/网页，自动建图谱，带引用对话。

## 前置条件

- Python 3.12+
- Node.js 22 LTS
- pnpm 9+
- Docker Desktop（Qdrant）
- NVIDIA RTX 3060 12GB + CUDA 12.x
- DeepSeek API Key

## 快速启动

```powershell
# 1. 初始化环境
.\scripts\setup_env.ps1

# 2. 配置密钥
copy .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 3. 启动基础设施
docker compose up -d qdrant

# 4. 启动开发服务（另开终端）
.\scripts\start_dev.ps1
```

访问 http://localhost:3000

## Windows 稳定启动（避免 `uv` 命令找不到）

若 PowerShell 提示 `uv` 不是内部或外部命令，请使用以下固定路径命令：

```powershell
cd C:\project_pg\stream-kg

# 终端1：Embedding 服务（先启动；国内默认走 ModelScope 下载模型）
& "$env:USERPROFILE\.local\bin\uv.exe" run python -m stream_kg.models.embedding_server
# 确认 http://localhost:8081/health 中 mode=real（若为 pseudo 见 .env 中 EMBEDDING_DOWNLOAD_SOURCE）

# 终端2：Backend
& "$env:USERPROFILE\.local\bin\uv.exe" run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# 终端3：Frontend
cd frontend
pnpm dev
```

可选：在当前 PowerShell 会话设置别名，后续直接使用 `uv`：

```powershell
Set-Alias uv "$env:USERPROFILE\.local\bin\uv.exe"
```

## Phase 功能

| Phase | 功能 | Feature Flags |
|-------|------|---------------|
| P1 | PDF/URL 导入、向量检索、RAG 对话 | 全部 `false` |
| P2 | 增量 KG、实体消解、图谱可视化 | `FEATURE_KG_ENABLED=true` |
| P3 | 混合检索、Reranker、引用跳转 | 全部 `true` |

## 文档

见 [docs/README.md](docs/README.md)

## 技术栈

- Backend: FastAPI + uv
- Frontend: Next.js 15 + Tailwind CSS 4 + Cytoscape.js
- Vector: Qdrant | Graph: NetworkX | Meta: SQLite
- PDF: MinerU 2.x (local GPU) | LLM: DeepSeek V4 Flash (API)
- Embedding/Reranker: Qwen3-0.6B (local GPU)
