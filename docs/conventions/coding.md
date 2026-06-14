# stream-kg — 编码规约

> **版本**：v0.2 | **状态**：已决议，可实施  
> **配套**：[docs/spec/](../spec/00-overview.md) | [docs/README.md](../README.md)

---

## 1. 项目结构

与仓库根目录一致，详见 `stream-kg/` tree。文档统一在 `docs/`：

```
docs/
├── README.md
├── spec/           # 技术规格（拆分）
├── conventions/    # 本文件
├── adr/
└── planning/
```

**禁止**：`requirements.txt`、根目录散落规格文档、`from module import *`、硬编码密钥。

---

## 2. 语言与运行时

| 组件 | 版本 | 包管理 |
|------|------|--------|
| Python | 3.12+ | uv（**uv.lock 提交仓库**） |
| Node.js | 22 LTS | pnpm 9+ |
| TypeScript | 5.7+ | strict |

---

## 3. Python

- **Ruff**：lint + format（`pyproject.toml`）
- **mypy**：P1 `check_untyped_defs`，P2 strict
- **类型**：所有 public 函数必须标注参数与返回值
- **Docstring**：Google style，仅 public API
- **异步**：I/O 用 async；CPU 密集用 `asyncio.to_thread()`
- **异常**：继承 `StreamKgError`，带 `error_code`
- **中文注释（强制）**：
  - 新增模块必须写中文模块说明（文件顶部）；
  - 所有 public 函数必须写中文 docstring（说明输入、输出、关键副作用）；
  - 关键流程代码段必须加中文行内注释（例如：状态流转、并发控制、降级分支）；
  - 注释需要解释“为什么这样做”，不能只重复代码字面含义。

---

## 4. TypeScript / 前端

- Next.js 15 App Router，`frontend/src/app/`
- 组件 `PascalCase.tsx`，named export
- Zustand 按 domain 拆分 store
- API 集中在 `src/lib/api.ts`，类型在 `src/lib/types.ts`
- Tailwind CSS 4，避免 inline style
- **中文注释（强制）**：
  - 页面级状态、异步流程、SSE 解析、轮询逻辑必须写中文注释；
  - 复杂交互（如 optimistic UI、流式拼接）必须写注释说明边界条件；
  - 注释优先写在逻辑块上方，保证 review 时能快速定位意图。

---

## 5. 依赖

```bash
uv add <pkg>          # Python
uv sync --all-extras
pnpm add <pkg>        # frontend
```

新依赖必须更新 `pyproject.toml` / `package.json` 并说明原因。

---

## 6. Git

- 分支：`feat/` `fix/` `refactor/` `docs/`
- Commit：Conventional Commits
- 不提交：`data/`、`.env`、`node_modules/`

---

## 7. 测试

| 模块 | 最低覆盖率 |
|------|-----------|
| `stream_kg/kg/` | 90% |
| `stream_kg/retrieval/` | 80% |
| `stream_kg/ingestion/` | 70% |

- 测试文件：`tests/unit/test_{module}.py`
- LLM / Embedding：**必须 mock**
- 核心算法：先写 test 再实现（TDD 优先）

```bash
uv run pytest tests/unit/ -v
uv run pytest --cov=stream_kg
```

---

## 8. Docker

- 仅 Qdrant 容器化；GPU 服务本地进程
- 健康检查见 `docker-compose.yml`

---

## 9. AI 编码护栏

| # | 规则 |
|---|------|
| 1 | 实现前读 `docs/spec/` 对应章节 |
| 2 | 100% 类型标注 |
| 3 | 配置从 `config.py` / `.env` 读取 |
| 4 | 核心模块同步写测试 |
| 5 | 单次变更 ≤ 500 行 |
| 6 | **Review** `graph_update.py`、`sqlite_store.py`、`qdrant_store.py` |
| 7 | P2/P3 用 Feature Flag，不污染 P1 |
| 8 | **不修改 spec 而不更新文档** |
| 9 | **新增/修改核心逻辑时必须补中文注释，缺注释视为不合格提交** |

### Prompt 模板

```
Implement {module} per docs/spec/{XX}.md and docs/conventions/coding.md
Phase: P1|P2|P3
Flags: {FEATURE_*}
```

---

## 10. ADR

重大决策写 `docs/adr/`。见 [001-networkx-over-neo4j.md](../adr/001-networkx-over-neo4j.md)。

---

## 11. Pre-commit

至少启用 **ruff** + **ruff-format**；pre-push 跑 `pytest tests/unit/`。

---

## 12. 已决议项

| 决策 | 结论 |
|------|------|
| LLM | DeepSeek-V3 API |
| 前端 | Next.js 15 |
| 包管理 | uv + pnpm |
| 脚本 | PowerShell `.ps1` |
| P1 图谱 | Empty state |
| 用户 | 单用户无 auth |

---

## 13. 文档先行流程（强制）

涉及 **API 契约/前端交互/存储级联** 的功能，必须按以下顺序执行：

1. 先更新 `docs/spec/`（至少包含 API、前端、存储影响）；
2. 在 PR 描述中标注“对应 spec 章节”；
3. 再进入代码实现与测试；
4. 若实现偏离 spec，必须先修订 spec 再合入代码。

### 当前已登记的文档先行需求

- 文档删除能力（单删 + 批删）：
  - API：`docs/spec/06-api.md`
  - 前端：`docs/spec/08-frontend.md`
  - 级联/图谱约束：`docs/spec/10-storage-schema.md`

---

*实现代码前必读 [docs/README.md](../README.md)。*
