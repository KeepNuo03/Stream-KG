"""图检索（Phase 2+）。"""

from __future__ import annotations

from collections import deque

from stream_kg.kg.graph_store import GraphStore


class GraphSearchService:
    """基于图节点与边证据收集补充 chunk。"""

    def __init__(self, *, graph_store: GraphStore) -> None:
        self.graph_store = graph_store

    async def entities_from_query(self, query: str, *, top_k: int = 5) -> list[str]:
        """按 query 词面匹配实体标签，返回候选实体 ID。"""
        tokens = [item.strip().lower() for item in query.split() if item.strip()]
        if not tokens:
            return []
        all_entities = await self.graph_store.export_graph(limit_nodes=500)
        scored: list[tuple[int, str]] = []
        for node in all_entities.get("nodes", []):
            label = str(node.get("label") or "").lower()
            if not label:
                continue
            score = sum(1 for token in tokens if token in label)
            if score > 0:
                scored.append((score, str(node.get("id"))))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entity_id for _, entity_id in scored[:top_k]]

    async def collect_chunks(self, *, entity_ids: list[str], hop: int) -> list[str]:
        """从图中按 hop 扩展实体并返回证据 chunk_id 列表。"""
        if not entity_ids:
            return []
        # 先导出全图做一次快照，避免多次锁访问。
        graph_data = await self.graph_store.export_graph(limit_nodes=5000)
        adjacency: dict[str, set[str]] = {}
        evidence_by_pair: dict[tuple[str, str], set[str]] = {}
        source_chunks: dict[str, set[str]] = {}
        for node in graph_data.get("nodes", []):
            node_id = str(node.get("id"))
            source_chunks[node_id] = set()
        for edge in graph_data.get("edges", []):
            source = str(edge.get("source"))
            target = str(edge.get("target"))
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
            key = (source, target)
            evidence = str(edge.get("evidence_chunk_id") or "")
            if evidence:
                evidence_by_pair.setdefault(key, set()).add(evidence)

        visited = set(entity_ids)
        queue = deque((entity_id, 0) for entity_id in entity_ids)
        collected_chunk_ids: set[str] = set()

        while queue:
            entity_id, depth = queue.popleft()
            if depth >= hop:
                continue
            for nxt in adjacency.get(entity_id, set()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))
                collected_chunk_ids.update(evidence_by_pair.get((entity_id, nxt), set()))
                collected_chunk_ids.update(evidence_by_pair.get((nxt, entity_id), set()))

        return sorted(collected_chunk_ids)
