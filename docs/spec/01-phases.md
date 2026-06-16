# 01 — 分阶段交付

## 当前状态（2026-06-16）

- MVP 主链路已可演示：导入 → 问答（citation）→ PDF 引用跳转 → 图谱查看/边解释 → citation 定位图谱实体。
- 双画布与 KG 增量能力已落地（含冲突边、Toast、关系解释、中文化 UI、文档性能优化）。
- 当前主要缺口已收敛到 Phase 4：多轮上下文记忆、历史会话列表、新建/切换会话。

## Phase 1 — Basic RAG（Week 1–2）

**Flags**: 全部 `false`

| 能力 | 说明 |
|------|------|
| PDF 上传 + MinerU 解析 | 本地 GPU |
| URL 网页导入 | Trafilatura |
| Chunk + Embedding + 向量检索 | Qwen3-Embedding 本地 |
| RAG 对话 + citations | DeepSeek-V3 API |
| 文档删除（单删/批删） | 前端选择 + 后端级联删除 |
| 三栏 UI | 图谱区 **empty state 占位** |

**验收**：

- [ ] 30 页 PDF ≤ **60s** 入库（修正自 30s）
- [ ] URL ≤ 10s 入库
- [ ] 对话带 `citations[]`
- [ ] 文档支持单个删除与批量删除（含失败明细）
- [ ] `GET /graph` 返回空图 + `placeholder: true`
- [ ] 前端图谱区显示 empty state 文案

## Phase 2 — Incremental KG（Week 3–4）

**Flags**: `FEATURE_KG_ENABLED=true`

| 能力 | 说明 |
|------|------|
| C1 在线实体消解 | Qdrant 候选检索 |
| C3 时序边 | improves/contradicts/extends/surveys |
| 图谱可视化 | Cytoscape.js |

**验收**：

- [ ] 第 2 篇 PDF 后自动合并重复实体
- [ ] 图谱 ≥ 10 节点、≥ 5 边
- [ ] 增量更新，不全量重建

## Phase 3 — Polish（Week 5）

**Flags**: 全部 `true`

| 能力 | 说明 |
|------|------|
| Query Router | 关键词 → hybrid（**MVP 阶段标记为 deferred**：graph 检索通道未启用前 router 无意义，避免空转） |
| Reranker | Qwen3-Reranker 本地 :8082 按需加载，`FEATURE_RERANKER_ENABLED` 控制启用 |
| 引用跳转 | citation chip + 弹窗展示完整 chunk + 「打开原 PDF」直链 |
| 对话↔图谱联动 v2 | citation 返回实体并支持一键定位图谱实体节点（命中失败回退文档锚定） |

**验收**：

- [ ] ~~「对比/演变」类 query 走 hybrid~~（MVP 不做，见 spec 12-agent-roadmap P4.1） |
- [x] Reranker 服务可启用：embedding/reranker 客户端均带熔断、失败不污染主链路
- [x] 点击 citation 跳转原文 chunk（弹窗）+ 一键打开原 PDF
- [x] citation 可一键定位到图谱实体节点（含无实体/未命中回退提示）

## Phase 4 — Agent Layer（Planned，不在 MVP）

**Status**: `planned` — 详见 [12-agent-roadmap.md](12-agent-roadmap.md)

**目标**：将单轨 RAG 升级为多轨道个人知识 Agent（闲聊 / 元数据 / 浏览 / 事实查询 / 任务 / 澄清 / 回忆），在 grounded 前提下实现自然对话，避免 R-017/R-018 类结构性误判；优先补齐多轮上下文记忆与历史会话能力。

**Flags（规划）**：`FEATURE_AGENT_ENABLED`（默认 false，实施时补充至 [07-config.md](07-config.md)）

| 能力 | 说明 |
|------|------|
| 意图分流 | Router + 规则 / 轻 LLM 路由 |
| Tool-Calling | 检索、读文档、元数据、动作统一为 tools |
| 会话状态注入 | 库摘要、最近上传、对话上下文 |
| Chitchat 通道 | 寒暄 / 通知 / 能力说明，不走 RAG |

**验收（P4 完成后）**：

- [ ] 「我现在传文档 一会儿问你」类 chitchat 自然回应
- [ ] 事实题仍 grounded + 引用；无证据不编造
- [ ] 元问题 / 浏览 / 查询各走正确通道，可观测 intent
- [ ] R-017 / R-018 回归用例全部通过

## Feature Flags

| Flag | P1 | P2 | P3 | P4（规划） |
|------|----|----|-----|-----------|
| `FEATURE_KG_ENABLED` | false | true | true | true |
| `FEATURE_RERANKER_ENABLED` | false | false | true | true |
| `FEATURE_GRAPH_ROUTER_ENABLED` | false | false | true | true |
| `FEATURE_AGENT_ENABLED` | false | false | false | true |
