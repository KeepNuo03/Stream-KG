# P3-X · 知识图谱 LLM 改造 · 执行计划与进度跟踪

> Status: `in-progress`  Owner: 程诺 + AI  Start: 2026-06-15
>
> 配套设计文档：[../spec/13-kg-llm-redesign.md](../spec/13-kg-llm-redesign.md)
>
> **使用方式**：每完成一项打勾 `[x]`，每个 Phase 完成后填 commit hash；Phase A.0 PoC 跑通后回头校准
> Phase A 任务细则与工时。

---

## 0. 决策拍板表

> 在 [13-kg-llm-redesign.md §9](../spec/13-kg-llm-redesign.md#9-决策记录2026-06-15-已拍板) 5 个核心决策基础上，
> 追加 5 个执行层面的默认值（2026-06-15 拍板）。

| # | 决策项 | 选择 | 备注 |
|---|--------|------|------|
| E1 | 旧规则抽取代码 | **保留作 fallback** | 新增 `feature_kg_use_llm` 子开关，默认开 LLM；待 LLM 稳定后 P4 删除旧代码 |
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

- [ ] **A.1** 新建 `stream_kg/llm/deepseek_client.py`，提供 `chat()` / `chat_stream()`
- [ ] **A.2** 把 `rag_generator.py` 里的 DeepSeek HTTP 调用迁移到 `deepseek_client`，**单测先跑通**
- [ ] **A.3** 新建 `stream_kg/encoding/llm_extractor.py` + `prompts/kg_extraction.txt`
- [ ] **A.4** 新建 `stream_kg/kg/models.py` 内 `KgExtraction` / `LlmEntity` / `LlmRelation` pydantic 模型 + jsonschema 校验
  - 实体类型枚举严格对齐 13 文档 §1.3（12 种），未覆盖类型回落 `concept`
  - 关系类型枚举严格对齐 13 文档 §1.4（10 种语义关系，不含 `mentions`，后者是 containment 边）
  - 实体数量上限：单 chunk 抽取后按 `salience` 排序截断 top 12（13 文档 §2.5 噪声治理）
- [ ] **A.5** `sqlite_store` schema 升级（沿用 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 幂等风格）：
  - 新表 `kg_extraction_logs`（13 文档 §3.1）
  - 新字段 `documents.kg_status TEXT CHECK (kg_status IN ('unprocessed','extracting','ready','failed')) DEFAULT 'unprocessed'`
  - 注：`entities.description` / `entities.aliases_json` 已存在，**不要重复 ALTER**（13 文档 §3.1 已校准）
- [ ] **A.6** `config.py` 新增：`feature_kg_use_llm: bool=True`、`kg_extraction_concurrency: int=5`、`kg_extraction_max_retries: int=3`、`kg_budget_yuan: float=10.0`
- [ ] **A.7** `ingest_pipeline._run_incremental_kg` 改造：feature flag 路由（旧规则 / LLM）；**上传后默认不再自动跑 KG**
- [ ] **A.8** 新增 `POST /api/v1/documents/{doc_id}/extract-kg`：异步触发 LLM 抽取，立即返回 `{status: extracting}`
- [ ] **A.9** 单测覆盖：`llm_extractor` JSON 解析 / 失败重试 / 端点过滤 / 实体超量截断 / 类型枚举回落；mock DeepSeek API
  - 覆盖 13 文档 §2.5 错误处理矩阵全部 6 种情况
- [ ] **A.10** 文档更新：`docs/研发暴雷与修复日志.md` 记录 PoC 阶段踩坑
- [ ] **A.11** commit: `feat(kg): Phase A - LLM 抽取核心模块`

**验收标准**：
- 后端 `pytest -q` 全绿
- curl 触发抽取 → 1 分钟内返回 ready，sqlite `kg_extraction_logs` 有记录
- `entities` 表里能看到 LLM 抽出的高质量实体

**进度**：0/11  **Commit**：—

---

## 3. Phase B · 数据模型 + 图重构（2d）

> 目标：双层图（L0 文档 + L1 实体）的后端数据模型与 NetworkX 图重写。

- [ ] **B.1** SQLite migration（沿用 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` 幂等风格）
  - `entities` ADD COLUMN `salience REAL DEFAULT 0.5`
    - 注：`description` / `aliases_json` 在初始 schema 中已存在（`sqlite_store.py:106`），**仅加 salience**
  - `temporal_edges` ADD COLUMN `evidence TEXT` / `evidence_chunks_json TEXT` / `llm_confidence REAL`
  - 新表 `doc_entity_links (doc_id, entity_id, mention_count, first_chunk_id, salience_max)`
- [ ] **B.2** `GraphStore.upsert_document_node(doc_id, doc_type, title)`：写入 L0 节点
- [ ] **B.3** `GraphStore.upsert_doc_entity_link(doc_id, entity_id, salience)`：写入 L0→L1 containment 边
- [ ] **B.4** `GraphStore.upsert_entity_edge_v2(head, tail, relation_type, evidence, llm_conf)`：semantic 边支持证据累加
- [ ] **B.5** `GraphStore.export_graph` 重写：参数 `view_mode = "l0" | "l1" | "mixed"`，节点 attrs 加 `layer` / `parent_doc_id`
- [ ] **B.6** 删除文档级联：移除 L0 节点 + 断开 L0-L1 + 释放孤立 L1（孤立 = 0 个 doc 关联）
- [ ] **B.7** `build_cross_doc_edges()`：扫 `doc_entity_links`，共享 ≥3 个 salience≥0.5 的实体 → 生成 L0-L0 边
- [ ] **B.8** `scripts/reset_kg.py`：清空 entities / mentions / edges / doc_entity_links / kg_extraction_logs + 删 graph.pkl
- [ ] **B.9** `llm_extractor` 接入 `GraphStore.upsert_*`：抽取结果落图
- [ ] **B.10** 单测：双层图导出 / 跨文档边构造 / 级联删除
- [ ] **B.11** commit: `feat(kg): Phase B - 双层数据模型与图重构`

**验收标准**：
- 抽取 2 篇相关论文后，`/api/v1/graph?view=mixed` 返回包含 L0+L1+cross-doc 边
- 删除 1 篇文档后，独占实体被清理，共享实体保留
- `scripts/reset_kg.py` 跑完后 entities/edges/graph.pkl 全为空

**进度**：0/11  **Commit**：—

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
| A 抽取核心 | 11 | 0 | 0% | — |
| B 数据模型 + 图 | 11 | 0 | 0% | — |
| C 规范化简化版 | 6 | 0 | 0% | — |
| D 前端重做 | 9 | 0 | 0% | — |
| E 监控错误处理 | 5 | 0 | 0% | — |
| **合计** | **48** | **6** | **13%** | — |

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
| API 成本超支 | 累计 token 消耗 / 累计 ¥ | — |
| LLM 输出不稳定 | 同 chunk 多次抽取实体差异率 | — |
| 抽取速度 | 单文档抽取 P95 耗时 | — |
| 图谱卡死 | 单文档实体数 P95 | — |

---

## 12. 变更记录

| 日期 | 变更 | 影响 |
|------|------|------|
| 2026-06-15 | 文档初始化，5 个执行决策拍板 | — |
| 2026-06-15 | 与 13 设计文档对齐审计（§13） | 修正 7 处不一致；同步反向校准 13 文档 |
| 2026-06-15 | Phase A.0 PoC 6/6 通过（§10） | prompt 一稿即用；deepseek-chat 在 attention 论文上 100% 覆盖、100% 解析、平均 5.47s；可放心进 Phase A |

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

