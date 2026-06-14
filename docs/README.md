# stream-kg 文档索引

> **Single Source of Truth**：实现代码必须遵循 `docs/spec/` 下的规格，不得偏离未记录的行为。

## 规格文档（Spec）

| 文档 | 内容 |
|------|------|
| [00-overview.md](spec/00-overview.md) | 项目目标、范围、已决议项 |
| [01-phases.md](spec/01-phases.md) | P1/P2/P3 交付计划与验收标准 |
| [02-hardware.md](spec/02-hardware.md) | RTX 3060 约束、本地/远程部署决策 |
| [03-architecture.md](spec/03-architecture.md) | 系统架构、模块边界、端口 |
| [04-data-models.md](spec/04-data-models.md) | 枚举、数据结构、状态机 |
| [05-algorithms.md](spec/05-algorithms.md) | C1/C3、Query Router、RAG |
| [06-api.md](spec/06-api.md) | REST + SSE API 契约 |
| [07-config.md](spec/07-config.md) | 环境变量、Feature Flags |
| [08-frontend.md](spec/08-frontend.md) | UI 布局、组件、交互 |
| [09-deployment.md](spec/09-deployment.md) | Docker、启动顺序、MinerU 集成 |
| [10-storage-schema.md](spec/10-storage-schema.md) | SQLite DDL、Qdrant Payload、删除级联 |
| [11-pipelines.md](spec/11-pipelines.md) | 异步 Ingest、QA 流水线伪代码 |
| [12-agent-roadmap.md](spec/12-agent-roadmap.md) | Phase 4 Agent 化升级路线（未来规划，不在 MVP） |

## 规约文档（Conventions）

| 文档 | 内容 |
|------|------|
| [coding.md](conventions/coding.md) | 编码风格、Git、测试、AI 护栏 |

## 架构决策（ADR）

| 文档 | 内容 |
|------|------|
| [001-networkx-over-neo4j.md](adr/001-networkx-over-neo4j.md) | 图存储选型 |

## 规划文档（Planning）

| 文档 | 内容 |
|------|------|
| [可行性评估与技术方案.md](planning/可行性评估与技术方案.md) | 项目可行性评估（第三方视角） |
| [项目评估提示词.md](planning/项目评估提示词.md) | 原始评估提示词 |

## 研发复盘（Incident Log）

| 文档 | 内容 |
|------|------|
| [研发暴雷与修复日志.md](研发暴雷与修复日志.md) | 研发期间问题发现过程、根因、修复与验证 |

## 已决议项（2026-06-07）

| 决策 | 结论 |
|------|------|
| LLM Provider | **DeepSeek-V3** API |
| 前端框架 | **Next.js 15** App Router |
| P1 图谱 UI | **Empty state 占位** |
| 用户模型 | **单用户**，无认证 |
| MinerU 失败 | **不 fallback**，标记 `failed` |
| 异步任务 | **FastAPI BackgroundTasks** + 轮询 |
| 实体候选检索 | **Qdrant** `entities` collection |
| 图存储 | **NetworkX + pickle**，`asyncio.Lock` |
