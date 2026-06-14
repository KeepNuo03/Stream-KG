# ADR-001: 使用 NetworkX 而非 Neo4j 作为 MVP 图存储

## Status

Accepted

## Context

MVP 阶段需要图存储支持节点/边 CRUD 和 2-hop 遍历。Neo4j 需要额外 Docker 容器，Windows 开发环境配置成本高。

## Decision

使用 NetworkX + `data/graph.pkl` 持久化，元数据存 SQLite `data/meta.db`。

- 并发：单进程 MVP，使用 `asyncio.Lock` 串行化图读写
- 候选检索：实体向量存 Qdrant `entities` collection，不依赖图数据库索引

## Consequences

**优点**

- 零额外容器，Windows + RTX 3060 开发友好
- AI 生成代码简单，符合 P1 快速验证目标

**缺点**

- 不支持大规模图（> 10K 节点需迁移）
- 无 Cypher，复杂图查询需手写 BFS
- 多进程不可共享图状态

## 迁移路径

产品化阶段可引入 Neo4j，通过 `graph_store.py` 适配层切换，SQLite 元数据 schema 保持不变。
