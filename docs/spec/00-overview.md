# 00 — 项目概述

> 版本 v0.2 | 状态：已决议，可实施

## 目标

构建本地运行的 **stream-kg** MVP Demo：

- 输入：PDF 论文 + 网页文章
- 处理：解析 → 向量化 → 增量知识图谱（P2+）→ 混合检索
- 输出：三栏 Web UI（文档库 / 知识图谱 / 带引用对话）

## MVP 范围内

见 [01-phases.md](01-phases.md)。

## MVP 范围外

- 视频 / 播客 / 浏览器插件
- 用户注册 / 多租户
- Neo4j 生产部署
- 论文评测流水线（`eval/` 后续单独开）
- SaaS 计费、公网 HTTPS

## 文档原则

1. `docs/spec/` 为实现唯一事实来源
2. 发现 spec 问题先更新文档，再改代码
3. P2/P3 功能通过 Feature Flag 隔离，不污染 P1 路径

## 相关文档

- 架构：[03-architecture.md](03-architecture.md)
- API：[06-api.md](06-api.md)
- 规约：[../conventions/coding.md](../conventions/coding.md)
