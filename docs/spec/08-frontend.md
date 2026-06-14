# 08 — 前端规格

> **实现状态（P2）**：`frontend/src/components/GraphCanvas.tsx` 已接入 Cytoscape.js 画布；`page.tsx` 支持文档/关系筛选、节点点击联动 `GET /graph/entities/{id}` 详情面板。

## 技术栈

- Next.js 15 App Router（`frontend/src/app/`）
- Tailwind CSS 4
- Zustand
- Cytoscape.js

## 三栏布局（≥1280px）

| 区域 | 宽度 | 组件 |
|------|------|------|
| InboxPanel | 280px | 上传、URL 导入、文档列表 |
| GraphView | flex-1 | 图谱 / **P1 empty state** |
| ChatPanel | 360px | 对话 + 引用卡片 |

## P1 Graph Empty State

当 `GET /graph` 返回 `placeholder: true`：

```
┌─────────────────────────┐
│   📊 知识图谱            │
│                         │
│  上传文档后，系统将自动   │
│  构建概念关联网络         │
│  （Phase 2 启用）        │
└─────────────────────────┘
```

## 组件目录

```
src/components/
├── layout/     Header, MainLayout
├── inbox/      UploadButton, DocumentList, DocumentCard
├── graph/      GraphView, GraphToolbar, EntityDetailDrawer
└── chat/       ChatPanel, MessageList, CitationCard
```

## 交互

| 操作 | 行为 |
|------|------|
| 上传 PDF | 进度 → 每 2s 轮询 doc status |
| 单个删除 | DocumentCard 右上角删除按钮，二次确认后调用单删除接口 |
| 批量删除 | 列表支持多选，批量删除按钮触发批删接口并展示成功/失败明细 |
| 点击 citation | P3：滚动到文档并高亮 chunk |
| 红色边 | `contradicts`，hover 显示 evidence |

## 文档删除交互规范（P1.1）

### 列表选择态

- 每个 `DocumentCard` 提供复选框（仅在非 `processing` 状态可选）。
- 列表头提供“全选当前页”复选框。
- 进入选择态后，顶部显示批量操作条：`已选 N 项` + `批量删除` + `取消选择`。

### 单个删除

- 入口：`DocumentCard` 删除图标按钮。
- 交互：弹确认框（显示文档标题 + 不可恢复提示）。
- 执行：调用 `DELETE /documents/{doc_id}`。
- 反馈：
  - 成功：移除卡片并 toast `已删除`；
  - 失败：toast + 保留卡片；
  - `processing`：禁用按钮并显示提示文案。

### 批量删除

- 入口：批量操作条 `批量删除` 按钮。
- 交互：弹确认框（显示将删除的数量）。
- 执行：调用 `POST /documents/batch-delete`。
- 反馈：
  - 全部成功：清空选择态并刷新列表；
  - 部分失败：展示失败清单（doc_id/标题 + 原因），成功项即时移除；
  - 全部失败：保留选择态，便于用户重试或取消。

### 与图谱阶段的前端约束（P2+ 预留）

- 当 `FEATURE_KG_ENABLED=true` 时，删除确认文案必须提示“将同时移除相关图谱实体/关系证据”。
- 删除完成后需触发图谱视图刷新（或局部失效）以避免悬挂节点显示。

## 状态 Store

- `documentStore` / `graphStore` / `chatStore`
- API 调用集中在 `src/lib/api.ts`
- 类型定义 `src/lib/types.ts` 对齐 `api/schemas.py`
