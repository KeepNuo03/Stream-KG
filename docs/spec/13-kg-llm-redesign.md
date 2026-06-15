# P3-X · 知识图谱 LLM 重设计（产品级双层图谱）

> Status: `design`  Owner: you + me  Last update: 2026-06-15
>
> 关联：R-020（规则抽取的天花板）、P4 Agent Roadmap（图谱作为 Agent 的事实存储层）

---

## 0. 为什么必须重做

### 当前规则抽取的根本短板

| 维度 | 现状 | 局限 |
|------|------|------|
| 实体识别 | 正则 `\b[A-Za-z]{4,}\b` + 大小写启发 + 停用词黑名单 | **没有任何语义理解**，`compute`/`queries`/`aligned`/`recurrent`/`architectures` 这类高频英文普通词全部入选；黑名单永远追在新名词后面跑 |
| 实体类型 | 二分法：method（含大写）/ concept（其他）| 无法识别 person / organization / dataset / metric / paper / time / location |
| 关系抽取 | 4-5 个动词模板（improve/extend/...）+ 默认 mentions | 95% 的边都是 mentions（共现），没有语义；模板覆盖不到 10% 真实关系 |
| 实体规范化 | canonical_id = hash(lower + strip) | 只能合并完全同名；BERT==Bert==B.E.R.T 这类 alias 无法识别 |

R-020 的工作（确定性 ID + 共现累加 + 噪声黑名单）**修对了三件事**——同名合并、confidence 累加、明显噪声词过滤——但**没修也修不了真正的问题：词都抓错了**。规则方法的天花板就在这里，再投入是低效的。

### 用户预期（提炼）

1. **双层节点结构**：文档/网页/视频/音频 = "大节点"；文档内的关键实体/概念 = "小节点"，从大节点辐射出去
2. **跨大节点关联**：不同文档可以通过共享实体或语义关系互联
3. **颜色编码**：按节点类型、关系类型上色
4. **产品级美观**：稳定布局（不抖动、不"虫子乱动"）、合理排版、清晰可读
5. **多模态可扩展**：未来视频/音频的关键点抽取走同一套数据模型

---

## 1. 设计理念

### 1.1 三层心智模型

```
                        Corpus（语料层）
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   📄 Document            🌐 Web             🎬 Video/🎵 Audio
   (PDF / Markdown)      (Webpage)           (Future)
        │                     │                     │
        ├── Entity            ├── Entity            ├── Entity
        ├── Entity            ├── Entity            ├── Concept
        └── Entity            └── Entity            └── Speaker
        
    L0: Source Nodes（大节点，每个导入物 = 1 个）
    L1: Entity Nodes（小节点，从 L0 辐射）
    L2: 跨源关联（Entity ↔ Entity 跨文档共享）
```

### 1.2 三类核心关系

| 关系层 | 形式 | 语义 | 视觉编码 |
|--------|------|------|----------|
| **L0-L1**：containment | doc ─contains→ entity | 文档包含该实体 | 浅灰虚线，无方向感 |
| **L1-L1**：semantic | entity ─proposes→ entity | LLM 抽出的语义关系 | 实线，按 relation_type 上色 |
| **L0-L0**：cross-doc | doc ─shares-entity→ doc | 共享 ≥ N 个实体 | 加粗灰线，hover 显示共享列表 |

### 1.3 节点类型 taxonomy

#### L0 大节点（Source）

| Type | 颜色 | 形状 | 来源 |
|------|------|------|------|
| `pdf` | 紫 `#7c3aed` | 圆角矩形 | 用户上传 PDF |
| `web` | 蓝 `#2563eb` | 圆角矩形 | URL 导入 |
| `video` | 橙 `#ea580c` | 圆角矩形 | P4 视频转文本 |
| `audio` | 绿 `#16a34a` | 圆角矩形 | P4 音频转文本 |
| `note` | 灰 `#475569` | 圆角矩形 | P4 用户手记 |

#### L1 小节点（Entity）

| Type | 颜色 | 形状 | 示例 |
|------|------|------|------|
| `person` | 金 `#ca8a04` | 圆形 | Vaswani / Andrej Karpathy / 程诺 |
| `organization` | 红 `#dc2626` | 圆形 | Google Brain / OpenAI / 清华大学 |
| `paper` | 靛 `#4338ca` | 圆形 | "Attention Is All You Need" |
| `method` | 蓝 `#2563eb` | 圆形 | Transformer / BERT / Self-attention |
| `concept` | 青 `#0891b2` | 圆形 | 多头注意力 / 残差连接 |
| `dataset` | 绿 `#16a34a` | 圆形 | WMT-14 / ImageNet |
| `metric` | 紫 `#9333ea` | 圆形 | BLEU / F1 / Accuracy |
| `task` | 粉 `#db2777` | 圆形 | 机器翻译 / 命名实体识别 |
| `location` | 灰 `#64748b` | 圆形 | 北京 / Mountain View |
| `time` | 灰 `#94a3b8` | 圆形 | 2017 / 2024 Q3 |
| `tool` | 橙 `#ea580c` | 圆形 | PyTorch / DeepSeek API |
| `role` | 棕 `#92400e` | 圆形 | 大模型应用开发工程师 / 算法实习生 |

> 不强求 13 种全用到，LLM prompt 里给一个开放枚举，未覆盖的类型走 `concept` 兜底。

### 1.4 关系类型 taxonomy

| Relation | 颜色 | 双向？ | 例子 |
|----------|------|--------|------|
| `proposes` | 绿 `#16a34a` | →有向 | Vaswani **proposes** Transformer |
| `improves` | 蓝 `#2563eb` | →有向 | Transformer **improves** RNN |
| `extends` | 青 `#0891b2` | →有向 | BERT **extends** Transformer |
| `contradicts` | 红 `#dc2626` | →有向 | Method A **contradicts** Method B |
| `part_of` | 靛 `#4338ca` | →有向 | Multi-Head Attention **part_of** Transformer |
| `uses` | 紫 `#9333ea` | →有向 | BERT **uses** WordPiece |
| `evaluates_on` | 粉 `#db2777` | →有向 | Transformer **evaluates_on** WMT-14 |
| `affiliated_with` | 金 `#ca8a04` | →有向 | Vaswani **affiliated_with** Google Brain |
| `authors` | 棕 `#92400e` | →有向 | Vaswani **authors** "Attention Is All You Need" |
| `co_occurs` | 浅灰 `#cbd5e1` | ↔无向 | 单次共现兜底（LLM 抽不到具体关系时）|
| `mentions` | 浅灰虚线 | →L0→L1 | doc **mentions** entity（containment 边专用）|

---

## 2. LLM 抽取流水线

### 2.1 抽取目标

对每个 chunk（约 512 token），LLM 返回严格 JSON：

```json
{
  "entities": [
    {
      "name": "Transformer",
      "type": "method",
      "aliases": ["self-attention model"],
      "description": "基于自注意力的序列建模架构",
      "salience": 0.95
    },
    {
      "name": "Vaswani et al.",
      "type": "person",
      "aliases": ["Ashish Vaswani"],
      "description": "Transformer 论文的第一作者"
    }
  ],
  "relations": [
    {
      "head": "Vaswani et al.",
      "relation": "proposes",
      "tail": "Transformer",
      "evidence": "Vaswani et al. proposed the Transformer architecture",
      "confidence": 0.92
    },
    {
      "head": "Transformer",
      "relation": "improves",
      "tail": "RNN",
      "evidence": "outperforms RNN on translation",
      "confidence": 0.85
    }
  ]
}
```

字段说明：
- `salience` (0~1)：LLM 自评该实体在 chunk 中的重要程度。低于 0.4 的不入图（避免一笔带过的引用）
- `evidence`：必填，用作未来 citation 的依据
- `confidence`：LLM 自评。我们再叠加共现/跨 chunk 一致性加权

### 2.2 Prompt 设计（草稿）

```
# Role
你是一名知识抽取专家。从下面的文档片段中抽取实体和关系，输出严格 JSON。

# 类型约束
entity.type ∈ {person, organization, paper, method, concept, dataset,
              metric, task, location, time, tool, role}
relation ∈ {proposes, improves, extends, contradicts, part_of, uses,
           evaluates_on, affiliated_with, authors, co_occurs}

# 规则
- 只抽对理解此文档有实质意义的实体，丢弃 the/and/data 等泛词
- 中英文都要抽，名字保留原文形式
- 同一实体的不同写法（BERT/Bert/B.E.R.T）放进 aliases
- 关系必须有 evidence 短语支撑，未提及则不要瞎编
- 输出**严格 JSON**，不要额外解释

# Few-shot
INPUT:
"Vaswani et al. (2017) proposed the Transformer, a self-attention based
model that outperforms recurrent networks on machine translation tasks
including WMT-14 English-to-German."

OUTPUT:
{"entities":[{"name":"Vaswani et al.","type":"person","aliases":["Ashish Vaswani"]},
             {"name":"Transformer","type":"method","aliases":["self-attention based model"],"salience":0.95},
             {"name":"WMT-14 English-to-German","type":"dataset","salience":0.6},
             {"name":"machine translation","type":"task","salience":0.7}],
 "relations":[{"head":"Vaswani et al.","relation":"proposes","tail":"Transformer","evidence":"Vaswani et al. (2017) proposed the Transformer","confidence":0.95},
              {"head":"Transformer","relation":"improves","tail":"recurrent networks","evidence":"outperforms recurrent networks","confidence":0.85},
              {"head":"Transformer","relation":"evaluates_on","tail":"WMT-14 English-to-German","evidence":"on machine translation tasks including WMT-14","confidence":0.8}]}

# Task
INPUT:
{{ chunk_text }}

OUTPUT:
```

> 一条 prompt 输入约 600 token（含 few-shot），输出约 200-500 token。

### 2.3 LLM 选型

| 候选 | 单次成本 | 速度 | 质量 | 选择理由 |
|------|---------|------|------|---------|
| **DeepSeek-V4** | 输入 ¥0.5/M token，输出 ¥1.5/M token | 流式 30-100 token/s | 中英双语强，已集成 | ✅ 首选 |
| Qwen-Max | 略贵 | 类似 | 同档次 | 备选 |
| 本地 Qwen2.5-7B | 0 | CPU 上 < 5 token/s | 弱于 DeepSeek-V4 | 弃 |
| 本地 Qwen2.5-72B | 0 | 需 80GB GPU | 顶级 | 等用户上 GPU 再考虑 |

成本预估（DeepSeek-V4）：
- attention 论文 15 chunk × (输入 1k + 输出 0.3k) ≈ 20k token ≈ **¥0.012**
- 简历 1 chunk ≈ **¥0.001**
- 100 篇论文 ≈ **¥1.2**

完全可承受。

### 2.4 调用策略

```
ingest_pipeline._run_incremental_kg():
  await llm_extractor.extract_batch(chunks)
    │
    ├── for chunk in chunks (asyncio.Semaphore(N=5)):
    │     ├── call_deepseek(prompt(chunk))
    │     ├── parse_json (jsonschema 校验)
    │     ├── if fail → retry up to 3 times with temp=0.0
    │     └── if still fail → mark chunk extraction_failed, persist 原始返回
    │
    ├── normalize_entities()
    │     ├── canonicalize name + type
    │     ├── merge aliases
    │     └── compute deterministic entity_id
    │
    ├── cross_chunk_alias_merge()
    │     ├── 同 type 同 canonical → 直接合并
    │     └── 同 type 不同 canonical 但 cosine(embed(name)) >= 0.88 → 合并 alias
    │
    ├── persist_to_graph()
    │     ├── upsert_document_node(doc_id, doc_type, title)
    │     ├── upsert_entity_nodes(entities)
    │     ├── upsert_doc_entity_edges (containment)
    │     └── upsert_entity_edges (semantic, with conf agg)
    │
    └── persist_to_sqlite (entity / mention / edge 表)
```

### 2.5 错误处理矩阵

| 失败类型 | 处理 | 用户感知 |
|---------|------|---------|
| LLM 返回非 JSON | retry 3 次 → fallback 到正则抽取（只抽 person/organization）| 该 chunk 实体偏少 |
| LLM 输出 entity.name 为空 | 直接丢弃 | 无 |
| LLM 输出 relation 端点不在 entities 列表 | 丢弃此 relation | 无 |
| LLM API 4xx | 抛异常 → 整个 chunk 标 failed | 文档行显示「LLM 抽取失败」，可重处理 |
| LLM API 5xx / 超时 | retry 3 次 | 速度下降 |
| LLM 输出大量噪声实体（> 20 个）| 按 salience 截到前 12 | 无 |

---

## 3. 数据模型升级

### 3.1 新增表（SQLite）

```sql
-- 文档级元信息（沿用 documents 表，仅新增 kg_status 字段）
ALTER TABLE documents ADD COLUMN kg_status TEXT
    CHECK (kg_status IN ('unprocessed','extracting','ready','failed'))
    DEFAULT 'unprocessed';

-- 实体表（升级现有 entities）
-- 注（2026-06-15 校准）：description / aliases_json 已在初始 schema 中存在
-- （见 stream_kg/storage/sqlite_store.py 中 entities 表定义），本次仅需新增 salience。
ALTER TABLE entities ADD COLUMN salience REAL DEFAULT 0.5;

-- 文档-实体关联表（新增）
CREATE TABLE doc_entity_links (
    doc_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 1,
    first_chunk_id TEXT,
    salience_max REAL DEFAULT 0.5,
    PRIMARY KEY (doc_id, entity_id)
);

-- 实体关系表（升级现有 temporal_edges）
ALTER TABLE temporal_edges ADD COLUMN evidence TEXT;  -- LLM 给的证据短语
ALTER TABLE temporal_edges ADD COLUMN evidence_chunks_json TEXT;  -- JSON array
ALTER TABLE temporal_edges ADD COLUMN llm_confidence REAL;  -- LLM 自评，与共现累加分开

-- LLM 抽取日志（新增，用于审计 / 调参）
CREATE TABLE kg_extraction_logs (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    entity_count INTEGER,
    relation_count INTEGER,
    raw_output TEXT,         -- LLM 原始输出（debug 用）
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    cost_yuan REAL
);
```

### 3.2 NetworkX 图结构

```python
# Node attrs
{
  "layer": "L0" | "L1",
  "node_type": "pdf" | "person" | "method" | ...,
  "label": str,
  "doc_ids": list[str],          # L1 节点：关联的所有文档
  "source_chunk_ids": list[str],
  "aliases": list[str],
  "description": str,
  "mention_count": int,
  "salience_max": float,
  "updated_at": iso8601,
}

# Edge attrs
{
  "edge_type": "containment" | "semantic" | "cross_doc",
  "relation_type": str,          # semantic 边专用
  "confidence": float,           # 综合：max(llm_conf) + log(count) 加权
  "llm_confidence_max": float,
  "evidence_count": int,
  "evidence_chunks": list[str],
  "evidence_text": str,          # 第一条 evidence 短语
}
```

---

## 4. 渲染层升级

### 4.1 布局换 fcose（解决「虫子乱动」）

- `cose` 是基础力导向，未收敛就渲染 → 视觉抖动
- 换 `fcose` (Fast Compound Spring Embedder)：
  - 收敛快（默认 ~1500 次迭代 → <1s）
  - 支持 `compound nodes`（双层结构原生支持）
  - 节点拖拽不重新跑布局
  - 安装：`npm i cytoscape-fcose`

布局参数草案：
```js
{
  name: "fcose",
  quality: "default",
  animate: false,          // 关键：算完再画，不要边算边动
  randomize: true,         // 初始随机，但因为 quality=default 会快速收敛
  nodeSeparation: 80,
  idealEdgeLength: edge => edge.data("relationType") === "containment" ? 60 : 120,
  nodeRepulsion: 9000,
  packComponents: true,    // 多连通分量自动分块排版
}
```

### 4.2 双层视觉策略

两种方案，选一个：

#### 方案 A：Compound（推荐）
- 文档节点作为 parent compound，实体作为 children
- L1 实体被"框"在 L0 文档框内（视觉上一眼分组）
- 跨文档共享的实体显示在 doc 外（无 parent）
- **优点**：分组清晰、Cytoscape 原生支持、布局自动避让
- **缺点**：跨文档实体的"归属"需要特殊处理

#### 方案 B：分层不分组
- L0 节点用大圆形，L1 节点小圆形
- 不用 compound，靠 fcose 的 `idealEdgeLength` 让 containment 边短、semantic 边长
- **优点**：实现简单
- **缺点**：分组不直观

→ **首选 A**，B 作为后备。

### 4.3 颜色编码（呼应 §1.3 / §1.4）

- 节点：按 `node_type` 字典查色
- 边：按 `relation_type` 字典查色（containment 边浅灰虚线）
- hover 节点：高亮其全部边 + 端点
- 选中节点：右侧详情面板（已有）+ 用 `description` 字段显示一句话简介

### 4.4 交互升级

| 交互 | 现状 | 目标 |
|------|------|------|
| 节点点击 | 加载详情 | 加载详情 + 高亮邻居 + 灰化无关 |
| 节点拖拽 | 重新跑布局 | 单节点 pin，不影响其他 |
| 缩放 | 滚轮 | 滚轮 + 双击缩放到节点 |
| 关系筛选 | 4 个固定档 | 按 relation_type 多选 chip |
| Layer 切换 | 无 | 顶部 toggle：「L0 文档关联视图」 / 「L1 实体网络」 / 「混合」|
| 搜索节点 | 无 | 顶部搜索框，回车 fit & 居中 |
| 导出 | 无 | 导出 PNG / SVG（fcose 支持）|

---

## 5. 实施路线图

### Phase A：LLM 抽取核心（2 天）

1. **A.1** 新增 `stream_kg/encoding/llm_extractor.py`
   - `LlmExtractor.extract_chunk(chunk) -> KgExtraction(entities, relations, raw)`
   - 调用 DeepSeek client，prompt 模板可配置
   - jsonschema 校验输出
2. **A.2** 新增 `prompts/kg_extraction.txt`（中英双语 few-shot）
3. **A.3** 新增 `KgExtraction` / `LlmEntity` / `LlmRelation` pydantic 模型
4. **A.4** 改 `ingest_pipeline._run_incremental_kg`：feature flag 切换 LLM / 规则
5. **A.5** 失败 chunk 落 `kg_extraction_logs` 表 + 文档级 `kg_status` 字段
6. **A.6** 接口 `POST /api/v1/documents/{doc_id}/extract-kg`：触发 LLM 抽取（首次或重抽，与 §9 决策 3 统一命名）

### Phase B：数据模型 + 图重构（2 天）

1. **B.1** SQLite migration（`alembic` 或手写）
2. **B.2** `GraphStore` 新增 `upsert_document_node` / `upsert_doc_entity_link`
3. **B.3** `GraphStore.export_graph` 重写：支持 `view_mode = "layer_l0" | "layer_l1" | "mixed"`
4. **B.4** 删除文档级联：移除 L0 节点 + 断开 L0-L1 + 释放孤立 L1
5. **B.5** `cross_doc_edges`：扫描共享实体生成 L0-L0 边（一次性 build job）

### Phase C：实体规范化 + alias 合并（1 天）

1. **C.1** 二次规范化：canonical name + type + aliases 标准化
2. **C.2** 跨 chunk / 跨文档 alias 合并：精确匹配优先，vector cosine 0.88 兜底
3. **C.3** 类型 disambiguation：同名不同类拆分（Apple-公司 vs Apple-水果）

### Phase D：前端图谱重做（2 天）

1. **D.1** `npm i cytoscape-fcose`
2. **D.2** `GraphCanvas` 重写：fcose + compound + 完整颜色字典
3. **D.3** 顶部 toggle：layer 切换 + 关系多选 chip + 搜索框
4. **D.4** 节点 hover 高亮邻居、单节点 pin、双击 fit
5. **D.5** 实体详情面板：加 `description` 一句话 + aliases chip + evidence

### Phase E：监控 + 错误处理（半天）

1. **E.1** `/api/v1/system/kg-stats`：抽取成功率、平均实体数、token 消耗、近 24h 失败 chunk 数
2. **E.2** 前端文档行显示 kg_status（unprocessed / extracting / done / failed），失败可重处理
3. **E.3** 抽取日志 CLI 工具：`scripts/inspect_kg_extraction.py --chunk <id>` 打印 LLM 原始输出

### Phase F（可选）：跨模态预留（不在 MVP）

- 视频/音频走「转文本 → LLM 抽取」复用 Phase A，doc_type 加 `video` / `audio`
- 视频额外抽取「时间戳-关键点」作为 L1 节点的 `time_anchor` 字段

---

## 6. 风险与权衡

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 输出不稳定 | 同一 chunk 不同次抽取结果差异 | temperature=0.0；严格 schema；保留 raw_output 审计 |
| 大文档抽取慢 | 100 chunk 论文 ≈ 5 min（即使并发 5）| 异步后台 + 文档级进度条；可配 `KG_EXTRACTION_CONCURRENCY` |
| API 成本失控 | 用户上传 1000 篇论文 | 配置月度预算上限 `KG_BUDGET_YUAN`，超额暂停 |
| LLM 抽错实体类型 | 把方法识别成数据集 | 后台对照规则做 type 校验；接受偶尔的偏差 |
| 图谱节点过多卡死前端 | 单文档抽 200+ 实体 | salience 截断到 30；前端按 salience 排序显示 top N |
| Compound 视图对跨文档实体的归属混乱 | 共享实体显示在哪个 doc 框里？ | 共享实体不放任何 compound 内，作为 L1 浮动节点，用边连各 doc |

---

## 7. 验收标准

- [ ] attention 论文抽取后：节点应包含 `Transformer` `Self-Attention` `Vaswani et al.` `RNN` `WMT-14` `BLEU`（人/方法/数据集/指标都覆盖）
- [ ] 关系应包含 `Vaswani -proposes-> Transformer` `Transformer -improves-> RNN` `Transformer -evaluates_on-> WMT-14`
- [ ] 简历抽取后：节点包含 `程诺` `大模型应用开发` `DeepSeek-V3` `ReAct` `Agent`
- [ ] 双层视图能看出「文档 A 和文档 B 通过共享实体 X / Y 关联」
- [ ] 100 节点图谱布局收敛 < 1.5s，节点拖拽流畅不重布局
- [ ] 颜色编码图例 + 关系筛选 + 节点搜索可用
- [ ] 单文档 LLM 抽取失败时，UI 可见状态 + 可一键重处理
- [ ] 抽取日志可追溯：任意 chunk 可查看 LLM 原始输出

---

## 8. 不在本次范围

- 跨文档实体合并的 entity alias 词典（人工维护）→ P4
- 图谱版本快照 / diff → P4
- GraphQL 接口 → P4+
- 多用户 / 多 workspace → 不做
- 视频/音频抽取 → P4+

---

## 9. 决策记录（2026-06-15 已拍板）

| # | 决策项 | 选择 | 含义 |
|---|--------|------|------|
| 1 | LLM 供应商 | **仅 DeepSeek** | 代码中不做 provider 抽象层；`llm_client.py` 直接调 DeepSeek API；未来切换时再做抽象 |
| 2 | 双层视图 | **Compound（A）** | Cytoscape compound nodes：L0 文档框住其 L1 实体；跨文档共享实体作为浮动 L1 节点不归属任何 compound |
| 3 | 抽取触发 | **手动触发** | 上传文档后状态为 `ready`；前端文档行显示「运行知识抽取」按钮；点击后状态 → `kg_extracting` → `kg_ready`；可单选或批量 |
| 4 | 失败处理 | **标记失败，不兜底** | failed chunk 落 `kg_extraction_logs`；前端可一键重试；不混用规则抽取，保证图谱质量一致 |
| 5 | L0↔L0 连线门槛 | **共享 ≥ 3 个高 salience 实体** | salience ≥ 0.5 的实体计入；满足才生成跨文档边；前端 hover 显示共享列表 |

### 设计影响

- 决策 1 → `stream_kg/llm/deepseek_client.py`（不是 `llm/client.py + providers/...`）
- 决策 2 → `GraphCanvas` 用 `parent` 字段构造 compound；`export_graph` 在节点 attrs 加 `parent_doc_id`
- 决策 3 → 后端新增 `POST /api/v1/documents/{doc_id}/extract-kg`；前端文档卡片新增「抽取知识」按钮 + 进度条
- 决策 4 → `kg_status` 字段：`unprocessed | extracting | ready | failed`；失败 chunk 单独可见可重试
- 决策 5 → `build_cross_doc_edges()` 函数：扫 `doc_entity_links`，按 salience 加权 join，输出 (doc_a, doc_b, shared_entities)，过滤 count >= 3

---

## 10. 工时与里程碑

| 阶段 | 工时（集中）| 完成后能看到 |
|------|------------|------------|
| A | 2 天 | LLM 抽取通路打通，控制台日志能看到结构化输出；图谱仍用旧渲染 |
| B | 2 天 | 后端 schema 升级、双层数据模型就绪，`/api/v1/graph?view=mixed` 返回新结构 |
| C | 1 天 | 同名实体合并、alias 工作正常 |
| D | 2 天 | 前端 fcose 双层视图 + 颜色编码 + 交互 |
| E | 0.5 天 | 失败可重处理、监控可见 |
| **合计** | **~7-8 天集中工作量** | 产品级图谱 |

按你"打磨"的节奏，分散到 1-2 周很从容。
