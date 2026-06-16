# P3-X · 知识图谱 LLM 改造 · 执行计划与进度跟踪

> Status: `mvp-delivered / p4-pending`  Owner: 程诺 + AI  Start: 2026-06-15
>
> 配套设计文档：[../spec/13-kg-llm-redesign.md](../spec/13-kg-llm-redesign.md)
>
> **使用方式**：每完成一项打勾 `[x]`，每个 Phase 完成后填 commit hash；Phase A.0 PoC 跑通后回头校准
> Phase A 任务细则与工时。
>
> **2026-06-16 快照**：
> - MVP 可演示闭环完成（双画布 + citation/PDF + 冲突 + 边解释 + citation→图谱实体联动）。
> - 工程化补强已完成一轮（文档列表/删除性能、PDF 查看器稳定性、中文化 UI）。
> - 下一优先级转向 P4：多轮上下文记忆、历史会话、新建/切换会话。

---

## 0. 决策拍板表

> 在 [13-kg-llm-redesign.md §9](../spec/13-kg-llm-redesign.md#9-决策记录2026-06-15-已拍板) 5 个核心决策基础上，
> 追加 5 个执行层面的默认值（2026-06-15 拍板）。

| # | 决策项 | 选择 | 备注 |
|---|--------|------|------|
| E1 | 旧规则抽取代码 | **保留作 fallback** | 新增 `feature_kg_use_llm` 子开关；**自动触发路径默认 False**（防误烧钱），手动 `POST /extract-kg` 永远走 LLM 路径（决策 3）。Phase A 落地时基于成本控制将默认从「True」改为「False」，由用户 `.env` 显式开启自动 LLM —— 详见 §13 audit #8 |
| E2 | 触发粒度 | **MVP 单文档手动触发** | 批量按钮放后期，避免引入队列/并发治理增加复杂度 |
| E3 | 旧 KG 数据 | **升级时清空** | 提供 `scripts/reset_kg.py` 一键清掉 `entities` / `entity_mentions` / `temporal_edges` / `graph.pkl`；旧规则数据本身质量差，留着混淆视听 |
| E4 | PoC 模型 | **`deepseek-chat` 起步** | 验证通路；prod 切回 `.env` 配的 `deepseek-v4-flash` |
| E5 | 抽取并发度 | **`KG_EXTRACTION_CONCURRENCY=5`** | 新增 settings 项；单论文 ~15-25s；过高有 429 风险 |

---

## 1. Phase A.0 · PoC 验证（0.5d）

> 目标：在不动主流程的前提下，跑通"DeepSeek → JSON 实体/关系"的端到端通路，
> 验证 prompt 质量、token 消耗、JSON 输出稳定性，**未通过 PoC 不进 Phase A**。

- [x] **A0.1** 新建 `scripts/test_kg_extract.py`：硬编码 attention 论文 1-2 个 chunk 作输入
- [x] **A0.2** prompt 草稿落地为 `prompts/kg_extraction.txt`（先按 13 文档 §2.2 的 few-shot 写一版）
- [x] **A0.3** 直接用 `httpx` 调 DeepSeek `/chat/completions`（暂不复用 rag_generator）
- [x] **A0.4** 解析返回 JSON、打印结构化结果、记录 token 消耗与耗时
- [x] **A0.5** 人工评估：实体覆盖率（Vaswani / Transformer / RNN / WMT-14 / BLEU 是否都抽到）+ 关系正确性
- [x] **A0.6** 评估通过 → 把 prompt 与典型输出附到本文档 §10 PoC 报告区

**验收标准**：
- attention 论文的关键实体（人/方法/数据集/指标）≥80% 抽到
- 关系至少包含 `Vaswani -proposes-> Transformer`
- JSON 解析成功率 ≥90%（3 次重跑）
- 单 chunk 抽取 < 8s（temp=0.0）

**进度**：6/6 ✅  **Commit**：（与 Phase A 同批 commit）

---

## 2. Phase A · LLM 抽取核心（1.5d）

> 目标：把 PoC 的脚本化抽取改造为可被 ingest pipeline 调用的服务模块。

- [x] **A.1** 新建 `stream_kg/llm/deepseek_client.py`，提供 `chat()` / `chat_raw()` / `chat_stream()`，统一 `DeepSeekError` 异常
- [x] **A.2** 把 `rag_generator.py` 里的 DeepSeek HTTP 调用迁移到 `deepseek_client`，**单测先跑通**（57 → 64 全绿）
- [x] **A.3** 新建 `stream_kg/encoding/llm_extractor.py` + `prompts/kg_extraction.txt`（PoC 复用，零迭代）
- [x] **A.4** 新建 `stream_kg/kg/llm_models.py`（与旧 `kg/models.py` 解耦）内 `KgExtraction` / `LlmEntity` / `LlmRelation` pydantic 模型
  - 实体类型枚举严格对齐 13 文档 §1.3（12 种）—— **新文件独立，不破坏旧 6 种 EntityType**
  - 关系类型枚举严格对齐 13 文档 §1.4（10 种语义关系，不含 `mentions`，后者是 containment 边）
  - 实体数量上限：单 chunk 抽取后按 `salience` 排序截断 top 12（13 文档 §2.5 噪声治理）
  - 治理三件套（model_validator）：salience<0.4 丢、超量截断、端点不在 entities 的 relation 丢
- [x] **A.5** `sqlite_store` schema 升级（沿用 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 幂等风格）：
  - 新表 `kg_extraction_logs`（13 文档 §3.1，含 chunk_id/doc_id 外键级联）
  - 新字段 `documents.kg_status TEXT DEFAULT 'unprocessed'` + `kg_error_message TEXT`
  - 新 helper：`set_document_kg_status` / `insert_kg_extraction_log` / `get_kg_extraction_stats`
  - 单测覆盖旧库 ALTER 迁移（**关键回归保护**）+ 幂等性
  - 注：`entities.description` / `entities.aliases_json` 已存在，**不要重复 ALTER**（13 文档 §3.1 已校准）
- [x] **A.6** `config.py` 新增：`feature_kg_use_llm: bool=False`（见 E1 修订）、`kg_extraction_concurrency: int=5`、`kg_extraction_max_retries: int=3`、`kg_extraction_max_tokens: int=2048`、`kg_extraction_timeout_sec: float=30.0`、`kg_budget_yuan: float=10.0`
- [x] **A.7** `ingest_pipeline.run_llm_extraction(doc_id)`：调 LlmExtractor → 写 kg_extraction_logs → 更 doc.kg_status；自动路径根据 `feature_kg_use_llm` 路由（True 走 LLM，False 走旧规则 fallback）；**Phase A 只写 logs 不上图**（图谱写入留给 Phase B）
- [x] **A.8** 新增 `POST /api/v1/documents/{doc_id}/extract-kg`（202 + 后台任务）+ `GET /{doc_id}/kg-stats`（监控）；并发保护：未 ready 文档 / 正在抽取 → 409
- [x] **A.9** 单测覆盖（47 个全绿，0 回归）：
  - `test_llm_models.py` (14)：pydantic 校验 + 治理三件套 + 端到端 attention sample
  - `test_llm_extractor.py` (11)：HTTP 重试 / JSON 解析重试 / schema 不重试 / batch 并发 / 部分失败
  - `test_sqlite_kg_schema.py` (7)：ALTER 幂等 + 旧库迁移 + status 全生命周期 + stats 汇总 + 级联删除
  - `test_ingest_llm_extraction.py` (7)：全成功 / 全失败 / 部分失败 / 中间态 extracting / 无 chunk / 文档不存在 / 抽取器 crash
  - `test_documents_extract_kg_endpoint.py` (8)：404 / 409×2 / 202 happy path / failed 可重试 / kg-stats 空 / kg-stats 汇总
- [x] **A.10** 文档更新：14 文档 §0 / §2 / §8 / §10 / §12 / §13 全部刷新（本次提交）；R-022 已在 PoC 阶段写入 `研发暴雷与修复日志.md`
- [x] **A.11** commit: `feat(kg): Phase A - LLM 抽取核心模块`

**验收标准**：
- ✅ 后端 `pytest -q` 全绿（64 → 79 passed，新增 47 个全绿，4 个 pre-existing 失败已记入 §11 backlog）
- ⏸ `entities` 表里能看到 LLM 抽出的高质量实体 —— **依赖 Phase B 入图，Phase A 只写 kg_extraction_logs**
- ⏸ curl 触发抽取 → 1 分钟内返回 ready，sqlite `kg_extraction_logs` 有记录 —— Phase A 已具备能力（路由 + 写 logs），待 Phase B commit 后 curl 端到端验证

**进度**：11/11 ✅  **Commit**：(本批 commit)

---

## 3. Phase B · 数据模型 + 图重构（2d）

> 目标：双层图（L0 文档 + L1 实体）的后端数据模型与 NetworkX 图重写。

- [x] **B.1** SQLite migration（沿用 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 幂等风格）
  - `entities` ADD COLUMN `salience REAL DEFAULT 0.5`
    - 注：`description` / `aliases_json` 在初始 schema 中已存在（`sqlite_store.py:106`），**仅加 salience**
  - `temporal_edges` ADD COLUMN `evidence TEXT` / `evidence_chunks_json TEXT` / `llm_confidence REAL`
  - 新表 `doc_entity_links (doc_id, entity_id, mention_count, first_chunk_id, salience_max)`
- [x] **B.2** `GraphStore.upsert_document_node(doc_id, doc_type, title)`：写入 L0 节点
- [x] **B.3** `GraphStore.upsert_doc_entity_link(doc_id, entity_id, salience)`：写入 L0→L1 containment 边
- [x] **B.4** `GraphStore.upsert_entity_edge_v2(head, tail, relation_type, evidence, llm_conf)`：semantic 边支持证据累加
- [x] **B.5** `GraphStore.export_graph` 重写：参数 `view_mode = "l0" | "l1" | "mixed"`，节点 attrs 加 `layer` / `parent_doc_id`
- [x] **B.6** 删除文档级联：移除 L0 节点 + 断开 L0-L1 + 释放孤立 L1（孤立 = 0 个 doc 关联）
- [x] **B.7** `build_cross_doc_edges()`：扫 `doc_entity_links`，共享 ≥3 个 salience≥0.5 的实体 → 生成 L0-L0 边
- [x] **B.8** `scripts/reset_kg.py`：清空 entities / mentions / edges / doc_entity_links / kg_extraction_logs + 删 graph.pkl
- [x] **B.9** `llm_extractor` 接入 `GraphStore.upsert_*`：抽取结果落图
- [x] **B.10** 单测：双层图导出 / 跨文档边构造 / 级联删除
- [x] **B.11** commit: `feat(kg): Phase B - 双层数据模型与图重构`

**验收标准**：
- 抽取 2 篇相关论文后，`/api/v1/graph?view=mixed` 返回包含 L0+L1+cross-doc 边
- 删除 1 篇文档后，独占实体被清理，共享实体保留
- `scripts/reset_kg.py` 跑完后 entities/edges/graph.pkl 全为空

**进度**：11/11 ✅  **Commit**：(本批 commit)

---

## 4. Phase C · 实体规范化（简化版，0.5d）

> 目标：MVP 期间只做必须的合并，复杂的 vector cosine 合并推迟到 D 之后。

- [ ] **C.1** `entity_normalizer.py`：canonical name 标准化（lower-strip + 去标点尾部）
- [ ] **C.2** 同名不同类型拆分（如 Apple-company vs Apple-fruit）→ canonical_id 加 type 前缀
- [ ] **C.3** aliases 去重 + 排序 + 截断（避免无限增长）
- [ ] **C.4** 单测：normalize 各 corner case
- [ ] **C.5** [defer P4] vector cosine 0.88 的跨 chunk alias 合并
- [ ] **C.6** commit: `feat(kg): Phase C - 实体规范化（简化版）`

**进度**：0/6  **Commit**：—

---

## 5. Phase D · 前端图谱重做（2d）

> 目标：fcose + compound 双层视图，颜色编码完整，交互升级。

- [ ] **D.1** `pnpm i cytoscape-fcose`（hover tooltip 用 Cytoscape 内置 `mouseover` 事件 + 自定义 div，不引入额外 popper 依赖）
- [ ] **D.2** `frontend/src/components/GraphCanvas.tsx` 重写：
  - 接入 fcose layout（animate:false, packComponents:true）
  - 节点根据 `parent_doc_id` 渲染为 compound children
  - 节点颜色按 `node_type` 字典查色：**12 类 entity**（13 文档 §1.3）+ **5 类 source**（pdf/web/video/audio/note）
  - 边颜色按 `relation_type` 字典查色：**10 类语义关系**（13 文档 §1.4）+ **1 类 containment**（`mentions`，浅灰虚线）
- [ ] **D.3** 顶部 toolbar 组件 `GraphToolbar.tsx`：
  - Layer toggle：L0 文档关联视图 / L1 实体网络 / 混合
  - Relation 多选 chip（按 relation_type 过滤）
  - 节点搜索框（回车 fit + 居中）
- [ ] **D.4** 节点交互：
  - Hover：高亮邻居 + 灰化无关
  - 单节点拖拽 pin（不重新跑 layout）
  - 双击：缩放到该节点
- [ ] **D.5** 详情面板升级（已有）：加 `description` 一句话 + aliases chip + evidence 引用
- [ ] **D.6** 颜色图例 `GraphLegend.tsx`：右下角折叠面板
- [ ] **D.7** 导出按钮：PNG / SVG（fcose 内置支持）
- [ ] **D.8** TS 编译干净（`tsc --noEmit`）+ ReadLints 干净
- [ ] **D.9** commit: `feat(kg-ui): Phase D - fcose 双层图谱 + 交互升级`

**验收标准**：
- 100 节点图谱布局收敛 < 1.5s
- 节点拖拽流畅不重布局
- Layer toggle 切换流畅
- 颜色编码与设计文档 §1.3/§1.4 一致

**进度**：0/9  **Commit**：—

> **2026-06-15 更新**：原 Phase C/D/E 已被「双画布 MVP 重构 P0–P7」吸收并完成主体交付：
> - P0 数据 bug 修复（LLM→SQLite 同步、repair_phase_b.py、默认 LLM 路径）✅
> - P1 思维导图（markmap + `/documents/{id}/mindmap`）✅
> - P2 KG 重做（fcose + layout cache + ego + 12 类配色 + GraphToolbar/Legend）✅
> - P3 点击边 LLM 解释 ✅
> - P4 聊天引用 PDF 跳转（react-pdf + 已有 `/documents/{id}/file`）✅
> - P5 增量 Toast（kg_change_events + 5s 轮询）✅
> - P6 冲突检测（LLM pairwise + conflict 边）✅
> - P7 单测 + 文档（R-027/028/029）✅
> 仍待后续：compound 父节点、hover 高亮邻居、PNG/SVG 导出、vector alias 合并（原 C.5）

---

## 6. Phase E · 监控 + 错误处理（0.5d）

> 目标：失败可见、可重试、可审计。

- [ ] **E.1** 后端 `GET /api/v1/system/kg-stats`：抽取成功率 / 平均实体数 / token 消耗累计 / 近 24h 失败 chunk 数
- [ ] **E.2** 前端文档卡片：`kg_status` badge（unprocessed/extracting/ready/failed）+ "抽取知识" / "重新抽取" 按钮
- [ ] **E.3** 前端文档卡片：抽取进度条（按 chunk 进度），轮询 `extract-kg` 接口
- [ ] **E.4** `scripts/inspect_kg_extraction.py`：CLI 工具，输入 chunk_id 打印 LLM raw_output
- [ ] **E.5** commit: `feat(kg): Phase E - KG 监控与失败重试`

**进度**：0/5  **Commit**：—

---

## 7. Phase F（可选 · 推迟）

> 不在本次 MVP 范围。占位提醒。

- [ ] **F.1** 视频/音频转文本 → 复用 LLM 抽取通路
- [ ] **F.2** 视频抽取「时间戳-关键点」作为 L1 节点的 `time_anchor`
- [ ] **F.3** 批量抽取按钮 + 任务队列

---

## 8. 总进度看板

| Phase | 任务数 | 完成 | 进度 | Commit |
|-------|--------|------|------|--------|
| A.0 PoC | 6 | 6 | ✅ 100% | (Phase A 同批 commit) |
| A 抽取核心 | 11 | 11 | ✅ 100% | (本批 commit) |
| B 数据模型 + 图 | 11 | 11 | ✅ 100% | (本批 commit) |
| C 规范化简化版 | 6 | 0 | 0%（保留待办） | — |
| D 前端重做 | 9 | 0 | 0%（主体并入 P0–P7 完成） | `811864c`, `0bad84d`, `bdf4889` |
| E 监控错误处理 | 5 | 0 | 0%（部分并入，核心待办保留） | `dcd36e6` |
| P3 后续补强（执行外增补） | 3 | 3 | ✅ 100% | `1d9f962`, `dcd36e6`, `e72a4a8` |
| **合计（原 48 项）** | **48** | **28** | **58%** | 详见上方并入说明 |

---

## 9. 验收清单（来自 13 文档 §7）

完成所有 Phase 后逐项打钩：

- [ ] attention 论文抽取后，包含 `Transformer` `Self-Attention` `Vaswani et al.` `RNN` `WMT-14` `BLEU` 节点
- [ ] 关系包含 `Vaswani -proposes-> Transformer` / `Transformer -improves-> RNN` / `Transformer -evaluates_on-> WMT-14`
- [ ] 简历抽取后包含 `程诺` `大模型应用开发` `DeepSeek-V3` `ReAct` `Agent`
- [ ] 双层视图能看出「文档 A 和文档 B 通过共享实体 X / Y 关联」
- [ ] 100 节点图谱布局收敛 < 1.5s，节点拖拽流畅不重布局
- [ ] 颜色编码图例 + 关系筛选 + 节点搜索可用
- [ ] 单文档 LLM 抽取失败时，UI 可见状态 + 可一键重处理
- [ ] 抽取日志可追溯：任意 chunk 可查看 LLM 原始输出

---

## 10. PoC 报告（Phase A.0 ✅ 2026-06-15）

### 测试配置
- **Model**: `deepseek-chat`（按 E4 决策）
- **Temperature**: 0.0
- **Response format**: `json_object`
- **Prompt**: [`prompts/kg_extraction.txt`](../../prompts/kg_extraction.txt)（4.2 KB，一稿通过未迭代）
- **样本**: attention 论文手写复刻 2 个 chunk（`abstract` 含作者信息 / `scaled_dot_product` 含公式）
- **重跑**: 每 chunk × 3 次 = 6 次调用
- **脚本**: [`scripts/test_kg_extract.py`](../../scripts/test_kg_extract.py)

### 汇总指标

| 指标 | 数值 | 验收阈值 | 结果 |
|------|------|----------|------|
| JSON 解析成功率 | 6/6 = 100% | ≥ 90% | ✅ PASS |
| 平均实体覆盖率 | 100% | ≥ 80% | ✅ PASS |
| 平均单 chunk 耗时 | 5.47s | < 8s | ✅ PASS |
| token 消耗（累计） | in=8133 / out=4161 | — | — |
| 预估成本 | 0.0103 CNY (6 次调用) | — | — |

### 各次调用明细

| chunk | run | parse | elapsed | tokens(in/out) | ent | rel | coverage |
|-------|-----|-------|---------|----------------|-----|-----|----------|
| abstract | 1 | OK | 6.52s | 1351/894 | 12 | 8 | 9/9 = 100% |
| abstract | 2 | OK | 6.61s | 1351/907 | 12 | 8 | 9/9 = 100% |
| abstract | 3 | OK | 6.34s | 1351/907 | 12 | 8 | 9/9 = 100% |
| scaled_dot_product | 1 | OK | 4.66s | 1360/491 | 6 | 5 | 6/6 = 100% |
| scaled_dot_product | 2 | OK | 4.18s | 1360/479 | 6 | 5 | 6/6 = 100% |
| scaled_dot_product | 3 | OK | 4.49s | 1360/483 | 6 | 5 | 6/6 = 100% |

> **稳定性观察**：abstract 3 次重跑 entity 数（12）和 relation 数（8）完全一致；scaled_dot_product 3 次 entity 数（6）和 relation 数（5）完全一致。
> `temperature=0.0 + response_format=json_object` 的组合让 DeepSeek 输出确定性极高，
> 后续 retry / 跨 chunk 合并不需要为输出抖动单独设计。

### 典型抽取结果（abstract chunk #1）

**实体**（12 个，按 salience 排序）：

| name | type | salience | aliases |
|------|------|----------|---------|
| Transformer | method | 0.95 | — |
| attention mechanism | concept | 0.70 | attention |
| machine translation | task | 0.70 | — |
| WMT 2014 English-to-German | dataset | 0.65 | WMT 2014 |
| recurrent neural networks | method | 0.60 | RNN |
| convolutional neural networks | method | 0.60 | CNN |
| BLEU | metric | 0.60 | — |
| Ashish Vaswani | person | 0.50 | — |
| Noam Shazeer | person | 0.50 | — |
| Niki Parmar | person | 0.50 | — |
| Google Brain | organization | 0.50 | — |
| Google Research | organization | 0.50 | — |

**关系**（8 条）：

| head | relation | tail | conf | evidence |
|------|----------|------|------|----------|
| Transformer | improves | recurrent neural networks | 0.80 | dispensing with recurrence and convolutions entirely |
| Transformer | improves | convolutional neural networks | 0.80 | dispensing with recurrence and convolutions entirely |
| Transformer | uses | attention mechanism | 0.95 | based solely on attention mechanisms |
| Transformer | evaluates_on | WMT 2014 English-to-German | 0.90 | Experiments on two machine translation tasks |
| Transformer | evaluates_on | BLEU | 0.90 | achieves 28.4 BLEU on the WMT 2014 |
| Ashish Vaswani | affiliated_with | Google Brain | 0.95 | Authors include Ashish Vaswani, Noam Shazeer ... |
| Noam Shazeer | affiliated_with | Google Brain | 0.95 | Authors include Ashish Vaswani, Noam Shazeer ... |
| Niki Parmar | affiliated_with | Google Research | 0.95 | Authors include Ashish Vaswani, Noam Shazeer ... |

### 踩坑结论

1. **Windows PowerShell 默认 GBK 编码**会把 `print("¥0.01")` 和中文 summary 字符串炸成 `UnicodeEncodeError`。
   修复：脚本顶部强制 `sys.stdout.reconfigure(encoding="utf-8")`，并把 stdout 里的 `¥` 改成 `CNY` 文本。
   *（已落到 13 文档对应的"研发暴雷与修复日志"R-022 待填项）*
2. **DeepSeek `response_format=json_object` 要求 user prompt 含 "json" 字样**——prompt 已经显式说"严格 JSON 对象"，满足。
3. **prompt 一稿通过**：完全按 13 文档 §2.2 的 few-shot 写，未发现需要迭代的点。Phase A 的 `prompts/kg_extraction.txt` 可直接复用该版本。

### Phase A 前置假设（可以信任的结论）

- ✅ DeepSeek-chat 满足 JSON 抽取质量需求
- ✅ 单 chunk 抽取约 5-7s，按 §5 决策的并发 5 跑 100-chunk 论文预计 ~100-140s
- ✅ 单论文成本极低：(8133 + 4161) tokens × 6 chunks → 100 chunks ≈ 0.17 CNY
- ✅ `temperature=0.0` 输出稳定，**不需要为重跑结果合并做去重设计**
- ⚠️ 待 Phase A 实现 Pydantic schema 时验证：单 chunk 实体数硬上限 12（按 prompt 约束）是否被 LLM 严格遵守

---

## 11. 风险与开销跟踪

| 风险 | 监控指标 | 当前状态 |
|------|---------|---------|
| API 成本超支 | 累计 token 消耗 / 累计 ¥ | A.0 PoC：6 次调用 ¥0.01；Phase A 已落 `kg_extraction_logs` 表 + `GET /kg-stats` 监控 endpoint |
| LLM 输出不稳定 | 同 chunk 多次抽取实体差异率 | A.0 PoC：3 次重跑实体/关系数 100% 一致；temperature=0.0 + json_object 确定性极高 |
| 抽取速度 | 单文档抽取 P95 耗时 | A.0 PoC：单 chunk 4-7s；按并发 5 估算 100-chunk 论文 ~100-140s |
| 图谱卡死 | 单文档实体数 P95 | A.4 治理：MAX_ENTITIES_PER_CHUNK=12 硬上限 + salience<0.4 过滤 + dangling relation 丢弃 |

### Backlog（Phase A 期间发现，非本期范围）

| # | 项目 | 影响 | 计划 |
|---|------|------|------|
| BL-1 | `tests/unit/test_entity_extractor.py` 2 个旧测试断言 `redis` 应保留为有效实体，但 R-020 加强黑名单后被过滤 | 旧规则路径回归保护缺失；不影响 LLM 路径 | Phase A 收尾后单独小 commit 修：要么更新断言、要么 R-020 黑名单加白名单豁免 |
| BL-2 | `tests/unit/test_graph_store{,_reload}.py` 2 个旧测试断言孤立节点应出现在 export，但 R-020 后默认过滤 | 旧规则路径 + 旧图 export 模式回归；Phase B 会重写 `export_graph` 引入 view_mode 三档，这两测试届时会被替换 | Phase B 重写 export_graph 时一并替换/删除 |

---

## 12. 变更记录

| 日期 | 变更 | 影响 |
|------|------|------|
| 2026-06-15 | 文档初始化，5 个执行决策拍板 | — |
| 2026-06-15 | 与 13 设计文档对齐审计（§13） | 修正 7 处不一致；同步反向校准 13 文档 |
| 2026-06-15 | Phase A.0 PoC 6/6 通过（§10） | prompt 一稿即用；deepseek-chat 在 attention 论文上 100% 覆盖、100% 解析、平均 5.47s；可放心进 Phase A |
| 2026-06-15 | **Phase A 11/11 落地** | 47 个新单测全绿，0 回归；总 79/83（4 pre-existing 失败收 backlog）；§13 audit #8 修订 E1 默认值；§11 加 Phase A 监控指标实测数 + Backlog 表 |
| 2026-06-15 | **Phase A smoke 端到端通过 + R-023 修复** | 14-chunk 论文真跑 24.4s / ¥0.033 / 143 entities + 104 relations / **14/14 ok**（修前 13/14）；R-023 修复 LLM 偶发凭空发明 entity/relation type 导致整 chunk 丢；4 个新单测 + §13 audit #11 |
| 2026-06-15 | **Phase B 11/11 落地 + 最小前端适配提前** | 双层图后端模型（L0/L1 + mixed/l0/l1 导出 + cross-doc）完成；LLM 抽取已落图；新增 reset_kg 脚本；文档列表新增 kg_status + 抽取按钮；图谱页新增 view_mode 切换，未到 Phase D 的复杂交互留后续 |
| 2026-06-16 | **MVP 收敛补强（稳定性 + 性能 + 体验）** | 完成中文化图例/关系解释、思维导图稳定性、PDF 查看器改为 iframe、文档列表与删除性能优化；关键提交：`bdf4889` / `1d9f962` / `dcd36e6` |
| 2026-06-16 | **对话↔图谱联动 v2 完成** | citation 增加实体信息并持久化，前端支持一键定位图谱实体（含回退提示）；关键提交：`e72a4a8` |

---

## 13. 与 13 设计文档的差异 / 校准记录

> 文档对齐 audit（2026-06-15）。已落地修复，仅作历史备查。

| # | 不一致点 | 13 文档原文 | 校准后 | 落地动作 |
|---|---|---|---|---|
| 1 | API endpoint 名 | §5 A.6 `/reextract` vs §9 决策 3 `/extract-kg`（**13 文档内部矛盾**）| 统一 `POST /api/v1/documents/{doc_id}/extract-kg` | 已改 13 §5；14 §2 A.8 已对齐 |
| 2 | `entities` 表 schema | §3.1 ALTER ADD `description` / `aliases_json` | 两字段已在初始 schema 存在，仅需 ADD `salience` | 已改 13 §3.1 加注脚；14 §2 A.5 / §3 B.1 已对齐 |
| 3 | `documents.kg_status` | §3.1 sql 块未提到 | 应在 schema 升级中显式 ALTER ADD | 已在 13 §3.1 补 ALTER；14 §2 A.5 已列入 |
| 4 | 实体类型数 | §1.3 列 12 种 | 12 类 entity（不是 13）| 已改 14 §5 D.2 |
| 5 | 关系类型数 | §1.4 列 11 行（含 mentions） | 10 类语义关系 + 1 类 containment (`mentions`) | 已改 14 §5 D.2 描述 |
| 6 | 错误处理覆盖 | §2.5 6 种 | A.4 / A.9 需显式覆盖噪声实体 top 12 截断 | 已扩 14 §2 A.4 / A.9 |
| 7 | 多余前端依赖 | §4.1 只要 cytoscape-fcose | hover tooltip 用 Cytoscape 内置事件即可 | 已删 14 §5 D.1 的 popper / @floating-ui/dom |
| 8 | E1 决策默认值 | 14 §0 表原写「`feature_kg_use_llm` 默认开 LLM」 | Phase A 落地时改为**默认 False**（自动路径走旧规则）；手动 `POST /extract-kg` 不受开关约束强制 LLM。理由：默认开 LLM 会让任何上传都触发 LLM 调用，对成本失控；手动触发是用户明确意图，决策 3 已拍板手动模式 | 已改 14 §0 E1 备注；Phase A 代码以"安全默认 + 显式开启"实现 |
| 9 | `documents` 新字段 | 13 §3.1 只列 `kg_status` | Phase A 实际加了 `kg_error_message`（前端展示失败原因 / 监控告警必需） | 已在 14 §2 A.5 备注；建议反向校准 13 §3.1 schema 块加一行 `kg_error_message TEXT` |
| 10 | 新文件位置 | 13 §3 把 `KgExtraction` pydantic 放 `stream_kg/kg/models.py` | 旧 `kg/models.py` 已有 `EntityType`（6 种 Literal），改它会连环 break entity_extractor / online_resolve 等 5 个文件 | Phase A 改为**新建 `stream_kg/kg/llm_models.py`** 与旧解耦；旧 6 种枚举保留给旧规则 fallback，新 12 种独立给 LLM 路径用。Phase B 删旧代码时一并清理 |
| 11 | 治理过严：单条非法 type 让整 chunk 失败 | 13 §2.5 错误处理矩阵未覆盖"LLM 凭空发明 type" | smoke 实测 14 chunk 真跑命中 1 次（7%），整 chunk 10+ entities 被连坐丢失；性价比极低 | R-023：`KgExtraction` 加 `mode='before'` validator —— 非法 entity type 回落 `concept`（保留实体），非法 relation type 丢这一条（不污染语义）。已落 `kg/llm_models.py` + 4 单测；重跑 smoke 14/14 ok。详见暴雷日志 R-023 |

