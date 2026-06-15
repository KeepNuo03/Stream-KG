"""NetworkX 图存储与持久化（Phase B 双层图版本）。"""

from __future__ import annotations

import asyncio
import pickle
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx

from stream_kg.encoding.entity_extractor import is_meaningful_graph_label, is_technical_entity_label
from stream_kg.encoding.entity_extractor import derive_entity_id
from stream_kg.kg.models import EntityMention, TemporalEdge


class GraphStore:
    """管理 `graph.pkl` 的读写与查询。"""

    def __init__(self, graph_path: str) -> None:
        self.graph_path = Path(graph_path)
        self._lock = asyncio.Lock()
        self._graph: nx.MultiDiGraph | None = None
        self._disk_mtime: float | None = None

    async def initialize(self) -> None:
        """初始化图对象（幂等）。"""
        async with self._lock:
            self._ensure_parent_dir()
            if self._graph is None:
                self._graph = nx.MultiDiGraph()
            await self._reload_from_disk_if_stale()

    async def persist(self) -> None:
        """持久化当前图到磁盘。"""
        async with self._lock:
            graph = self._require_graph()
            self._ensure_parent_dir()
            with self.graph_path.open("wb") as fp:
                pickle.dump(graph, fp)
            if self.graph_path.exists():
                self._disk_mtime = self.graph_path.stat().st_mtime

    async def _reload_from_disk_if_stale(self) -> None:
        """若磁盘 graph.pkl 比内存新，则重新加载（修复 ingest/API 多实例不同步）。"""
        if not self.graph_path.exists():
            return
        mtime = self.graph_path.stat().st_mtime
        if self._disk_mtime is not None and mtime <= self._disk_mtime:
            return
        try:
            with self.graph_path.open("rb") as fp:
                loaded = pickle.load(fp)
            if isinstance(loaded, nx.MultiDiGraph):
                self._graph = loaded
                self._disk_mtime = mtime
        except Exception:
            pass

    async def upsert_entity_from_mention(
        self,
        *,
        entity_id: str,
        canonical_name: str,
        entity_type: str,
        mention: EntityMention,
    ) -> None:
        """基于 mention 更新实体节点元信息（旧接口，兼容 fallback 规则抽取）。"""
        await self.upsert_entity_node(
            entity_id=entity_id,
            canonical_name=canonical_name,
            entity_type=entity_type,
            doc_id=mention.doc_id,
            chunk_id=mention.chunk_id,
            aliases=[mention.surface_form],
            salience=1.0,
            description=None,
            parent_doc_id=mention.doc_id,
        )

    async def upsert_document_node(self, *, doc_id: str, doc_type: str, title: str) -> None:
        """Upsert L0 文档节点。"""
        async with self._lock:
            graph = self._require_graph()
            graph.add_node(
                f"doc::{doc_id}",
                id=f"doc::{doc_id}",
                layer="L0",
                label=title,
                doc_id=doc_id,
                doc_type=doc_type,
                parent_doc_id=None,
                mention_count=0,
                entity_type="document",
            )

    async def upsert_entity_node(
        self,
        *,
        entity_id: str,
        canonical_name: str,
        entity_type: str,
        doc_id: str,
        chunk_id: str,
        aliases: list[str] | None = None,
        salience: float = 0.5,
        description: str | None = None,
        parent_doc_id: str | None = None,
    ) -> None:
        """Upsert L1 实体节点（双层图主接口）。"""
        async with self._lock:
            graph = self._require_graph()
            existing = graph.nodes.get(entity_id, {})
            doc_ids = set(existing.get("doc_ids", []))
            chunk_ids = set(existing.get("source_chunk_ids", []))
            alias_set = set(existing.get("aliases", []))
            doc_ids.add(doc_id)
            chunk_ids.add(chunk_id)
            for alias in aliases or []:
                if alias:
                    alias_set.add(alias)
            mention_count = int(existing.get("mention_count", 0)) + 1
            prev_salience = float(existing.get("salience") or 0.0)
            graph.add_node(
                entity_id,
                id=entity_id,
                layer="L1",
                label=canonical_name,
                entity_type=entity_type,
                doc_ids=sorted(doc_ids),
                source_chunk_ids=sorted(chunk_ids),
                aliases=sorted(alias_set),
                description=description or str(existing.get("description") or ""),
                salience=max(prev_salience, salience),
                mention_count=mention_count,
                parent_doc_id=parent_doc_id,
            )

    async def upsert_doc_entity_link(
        self,
        *,
        doc_id: str,
        entity_id: str,
        salience: float = 0.5,
        mention_count_delta: int = 1,
    ) -> None:
        """Upsert L0(doc) -> L1(entity) containment 边（relation_type='mentions'）。"""
        async with self._lock:
            graph = self._require_graph()
            doc_node = f"doc::{doc_id}"
            if doc_node not in graph.nodes:
                graph.add_node(
                    doc_node,
                    id=doc_node,
                    layer="L0",
                    label=doc_id,
                    doc_id=doc_id,
                    doc_type="unknown",
                    parent_doc_id=None,
                    mention_count=0,
                    entity_type="document",
                )
            existing = graph.get_edge_data(doc_node, entity_id, "mentions")
            prev_conf = float(existing.get("confidence") or 0.0) if existing else 0.0
            prev_count = int(existing.get("evidence_count") or 0) if existing else 0
            graph.add_edge(
                doc_node,
                entity_id,
                key="mentions",
                edge_id=f"{doc_node}->{entity_id}::mentions",
                relation_type="mentions",
                confidence=max(prev_conf, salience),
                evidence_count=prev_count + max(mention_count_delta, 1),
                layer="L0-L1",
            )

    async def upsert_entity_edge_v2(
        self,
        *,
        head_entity_id: str,
        tail_entity_id: str,
        relation_type: str,
        confidence: float,
        evidence: str | None = None,
        evidence_chunk_id: str | None = None,
    ) -> None:
        """Upsert L1-L1 语义边（Phase B 新接口，支持证据累计）。"""
        async with self._lock:
            graph = self._require_graph()
            if head_entity_id == tail_entity_id:
                return
            key = relation_type
            existing = graph.get_edge_data(head_entity_id, tail_entity_id, key)
            prev_conf = float(existing.get("confidence") or 0.0) if existing else 0.0
            prev_count = int(existing.get("evidence_count") or 0) if existing else 0
            prev_chunks = list(existing.get("evidence_chunks") or []) if existing else []
            if evidence_chunk_id and evidence_chunk_id not in prev_chunks:
                prev_chunks.append(evidence_chunk_id)
            graph.add_edge(
                head_entity_id,
                tail_entity_id,
                key=key,
                edge_id=existing.get("edge_id") if existing else f"{head_entity_id}->{tail_entity_id}::{relation_type}",
                relation_type=relation_type,
                confidence=max(prev_conf, confidence),
                llm_confidence=max(prev_conf, confidence),
                evidence=evidence or str(existing.get("evidence") or "") if existing else (evidence or ""),
                evidence_chunk_id=evidence_chunk_id or (existing.get("evidence_chunk_id") if existing else ""),
                evidence_count=prev_count + 1,
                evidence_chunks=prev_chunks,
                layer="L1-L1",
            )

    async def add_temporal_edge(self, edge: TemporalEdge) -> None:
        """兼容旧接口：TemporalEdge -> upsert_entity_edge_v2。

        R-020：之前用 edge_id 作为 networkx multi-edge key，导致同样的关系被重复
        记录无数遍，confidence 永远是单次值。改为按 (head, tail, rel) 合并，
        新 confidence 取较大值，evidence_count 累加。
        """
        await self.upsert_entity_edge_v2(
            head_entity_id=edge.head_entity_id,
            tail_entity_id=edge.tail_entity_id,
            relation_type=edge.relation_type,
            confidence=edge.confidence,
            evidence=None,
            evidence_chunk_id=edge.evidence_chunk_id,
        )

    async def build_cross_doc_edges(
        self,
        *,
        doc_entity_links: list[dict[str, Any]],
        min_shared_entities: int = 3,
        min_salience: float = 0.5,
    ) -> int:
        """基于 doc_entity_links 生成 L0-L0 跨文档边（shares_entity）。"""
        async with self._lock:
            graph = self._require_graph()
            doc_to_entities: dict[str, set[str]] = {}
            for row in doc_entity_links:
                if float(row.get("salience_max") or 0.0) < min_salience:
                    continue
                doc = str(row.get("doc_id") or "")
                ent = str(row.get("entity_id") or "")
                if not doc or not ent:
                    continue
                doc_to_entities.setdefault(doc, set()).add(ent)
            count = 0
            for d1, d2 in combinations(sorted(doc_to_entities.keys()), 2):
                shared = sorted(doc_to_entities[d1] & doc_to_entities[d2])
                if len(shared) < min_shared_entities:
                    continue
                n1 = f"doc::{d1}"
                n2 = f"doc::{d2}"
                if n1 not in graph.nodes or n2 not in graph.nodes:
                    continue
                graph.add_edge(
                    n1,
                    n2,
                    key="shares_entity",
                    edge_id=f"{n1}<->{n2}::shares_entity",
                    relation_type="shares_entity",
                    confidence=min(1.0, len(shared) / 10),
                    shared_entities=shared,
                    shared_count=len(shared),
                    layer="L0-L0",
                )
                count += 1
            return count

    async def export_graph(
        self,
        *,
        doc_id: str | None = None,
        view_mode: str = "mixed",
        limit_nodes: int = 36,
        min_mentions: int = 2,
        relation_type: str | None = "balanced",
        filter_noise: bool = True,
        max_edges: int = 48,
    ) -> dict[str, Any]:
        """导出图谱节点/边给 API 层。

        - view_mode='l1': 仅实体网（兼容旧逻辑）
        - view_mode='l0': 仅文档网（cross-doc 边）
        - view_mode='mixed': 文档+实体混合视图（L0-L1-L1）
        """
        if view_mode not in {"l0", "l1", "mixed"}:
            view_mode = "mixed"
        await self.initialize()
        async with self._lock:
            await self._reload_from_disk_if_stale()
            graph = self._require_graph()
            if view_mode == "l1":
                nodes, edges, stats = self._export_l1(
                    graph=graph,
                    doc_id=doc_id,
                    limit_nodes=limit_nodes,
                    min_mentions=min_mentions,
                    relation_type=relation_type,
                    filter_noise=filter_noise,
                    max_edges=max_edges,
                )
            elif view_mode == "l0":
                nodes, edges, stats = self._export_l0(
                    graph=graph,
                    doc_id=doc_id,
                    limit_nodes=limit_nodes,
                    max_edges=max_edges,
                )
            else:
                nodes, edges, stats = self._export_mixed(
                    graph=graph,
                    doc_id=doc_id,
                    limit_nodes=limit_nodes,
                    min_mentions=min_mentions,
                    relation_type=relation_type,
                    filter_noise=filter_noise,
                    max_edges=max_edges,
                )
            return {
                "nodes": nodes,
                "edges": edges,
                "stats": stats,
                "placeholder": False,
            }

    def _export_l0(
        self,
        *,
        graph: nx.MultiDiGraph,
        doc_id: str | None,
        limit_nodes: int,
        max_edges: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        doc_nodes = [nid for nid, data in graph.nodes(data=True) if data.get("layer") == "L0"]
        if doc_id:
            doc_nodes = [nid for nid in doc_nodes if graph.nodes[nid].get("doc_id") == doc_id]
        doc_nodes = doc_nodes[: max(1, limit_nodes)]
        doc_set = set(doc_nodes)
        edges: list[dict[str, Any]] = []
        for head, tail, key, data in graph.edges(keys=True, data=True):
            if head not in doc_set or tail not in doc_set:
                continue
            if str(data.get("relation_type")) != "shares_entity":
                continue
            edges.append(
                {
                    "id": str(data.get("edge_id") or key),
                    "source": head,
                    "target": tail,
                    "source_label": str(graph.nodes[head].get("label") or head),
                    "target_label": str(graph.nodes[tail].get("label") or tail),
                    "relation_type": "shares_entity",
                    "confidence": float(data.get("confidence") or 0.0),
                    "shared_count": int(data.get("shared_count") or 0),
                    "shared_entities": list(data.get("shared_entities") or []),
                    "layer": "L0-L0",
                }
            )
        edges = sorted(edges, key=lambda e: e["confidence"], reverse=True)[: max(1, max_edges)]
        nodes = [
            {
                "id": nid,
                "label": str(graph.nodes[nid].get("label") or nid),
                "type": "document",
                "layer": "L0",
                "doc_id": str(graph.nodes[nid].get("doc_id") or "").strip(),
                "parent_doc_id": None,
                "doc_count": 1,
                "mention_count": int(graph.nodes[nid].get("mention_count") or 0),
            }
            for nid in doc_nodes
        ]
        return nodes, edges, {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relation_profile": "shares_entity",
            "applied_min_mentions": 0,
            "view_mode": "l0",
        }

    def _export_l1(
        self,
        *,
        graph: nx.MultiDiGraph,
        doc_id: str | None,
        limit_nodes: int,
        min_mentions: int,
        relation_type: str | None,
        filter_noise: bool,
        max_edges: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        applied_min_mentions = min_mentions
        selected_ids: list[str] = []
        edges: list[dict[str, Any]] = []
        label_by_id: dict[str, str] = {}
        for candidate_min in [min_mentions, max(min_mentions - 1, 1), 1]:
            node_pool = self._select_node_pool(
                graph=graph,
                doc_id=doc_id,
                min_mentions=candidate_min,
                filter_noise=filter_noise,
            )
            label_by_id = {
                node_id: str(graph.nodes[node_id].get("label") or node_id)
                for node_id in node_pool
            }
            selected_ids, edges = self._build_connected_export(
                graph=graph,
                pool=set(node_pool),
                label_by_id=label_by_id,
                relation_type=relation_type or "balanced",
                max_edges=max(1, max_edges),
                limit_nodes=max(limit_nodes, 1),
            )
            applied_min_mentions = candidate_min
            if selected_ids:
                break
        nodes = [
            {
                "id": node_id,
                "label": label_by_id.get(node_id, node_id),
                "type": str(graph.nodes[node_id].get("entity_type") or "concept"),
                "layer": "L1",
                "parent_doc_id": graph.nodes[node_id].get("parent_doc_id"),
                "doc_count": len(graph.nodes[node_id].get("doc_ids", [])),
                "mention_count": int(graph.nodes[node_id].get("mention_count", 0)),
            }
            for node_id in selected_ids
        ]
        return nodes, edges, {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relation_profile": relation_type or "balanced",
            "applied_min_mentions": applied_min_mentions,
            "view_mode": "l1",
        }

    def _export_mixed(
        self,
        *,
        graph: nx.MultiDiGraph,
        doc_id: str | None,
        limit_nodes: int,
        min_mentions: int,
        relation_type: str | None,
        filter_noise: bool,
        max_edges: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        l1_nodes, l1_edges, l1_stats = self._export_l1(
            graph=graph,
            doc_id=doc_id,
            limit_nodes=limit_nodes,
            min_mentions=min_mentions,
            relation_type=relation_type,
            filter_noise=filter_noise,
            max_edges=max_edges,
        )
        entity_ids = {node["id"] for node in l1_nodes}
        containment_edges: list[dict[str, Any]] = []
        doc_nodes_needed: set[str] = set()
        for head, tail, key, data in graph.edges(keys=True, data=True):
            if str(data.get("relation_type")) != "mentions":
                continue
            if head.startswith("doc::") and tail in entity_ids:
                doc_nodes_needed.add(head)
                containment_edges.append(
                    {
                        "id": str(data.get("edge_id") or key),
                        "source": head,
                        "target": tail,
                        "source_label": str(graph.nodes[head].get("label") or head),
                        "target_label": str(graph.nodes[tail].get("label") or tail),
                        "relation_type": "mentions",
                        "confidence": float(data.get("confidence") or 0.0),
                        "layer": "L0-L1",
                    }
                )
        doc_nodes = [
            {
                "id": nid,
                "label": str(graph.nodes[nid].get("label") or nid),
                "type": "document",
                "layer": "L0",
                "doc_id": str(graph.nodes[nid].get("doc_id") or "").strip(),
                "parent_doc_id": None,
                "doc_count": 1,
                "mention_count": 0,
            }
            for nid in sorted(doc_nodes_needed)
        ]
        nodes = doc_nodes + l1_nodes
        edges = containment_edges + l1_edges
        if len(edges) > max_edges * 2:
            edges = edges[: max_edges * 2]
        return nodes, edges, {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relation_profile": relation_type or "balanced",
            "applied_min_mentions": l1_stats["applied_min_mentions"],
            "view_mode": "mixed",
        }

    async def get_entity_detail(self, entity_id: str) -> dict[str, Any] | None:
        """返回单实体详情与邻接边。"""
        await self.initialize()
        async with self._lock:
            await self._reload_from_disk_if_stale()
            graph = self._require_graph()
            if entity_id not in graph:
                return None
            node = graph.nodes[entity_id]
            relations: list[dict[str, Any]] = []
            for _, tail, key, data in graph.out_edges(entity_id, keys=True, data=True):
                relations.append(
                    {
                        "edge_id": str(data.get("edge_id") or key),
                        "direction": "out",
                        "relation_type": str(data.get("relation_type") or "mentions"),
                        "target_entity_id": tail,
                        "confidence": float(data.get("confidence") or 0.0),
                        "evidence_chunk_id": str(data.get("evidence_chunk_id") or ""),
                    }
                )
            for head, _, key, data in graph.in_edges(entity_id, keys=True, data=True):
                relations.append(
                    {
                        "edge_id": str(data.get("edge_id") or key),
                        "direction": "in",
                        "relation_type": str(data.get("relation_type") or "mentions"),
                        "source_entity_id": head,
                        "confidence": float(data.get("confidence") or 0.0),
                        "evidence_chunk_id": str(data.get("evidence_chunk_id") or ""),
                    }
                )
            return {
                "entity_id": entity_id,
                "label": str(node.get("label") or entity_id),
                "entity_type": str(node.get("entity_type") or "concept"),
                "layer": str(node.get("layer") or "L1"),
                "aliases": node.get("aliases", []),
                "doc_ids": node.get("doc_ids", []),
                "source_chunk_ids": node.get("source_chunk_ids", []),
                "mention_count": int(node.get("mention_count") or 0),
                "relations": relations,
            }

    async def entity_ids(self) -> list[str]:
        """返回全部实体 ID。"""
        async with self._lock:
            graph = self._require_graph()
            return list(graph.nodes())

    async def remove_document(self, doc_id: str, *, chunk_ids: list[str] | None = None) -> list[str]:
        """从图中移除指定文档的节点元数据与相关边。

        返回已从图中完全移除的实体 ID（该实体不再关联任何文档）。
        """
        chunk_set = set(chunk_ids or [])
        async with self._lock:
            graph = self._require_graph()
            doc_node_id = f"doc::{doc_id}"

            edges_to_remove: list[tuple[str, str, str]] = []
            for head, tail, key, data in graph.edges(keys=True, data=True):
                evidence = str(data.get("evidence_chunk_id") or "")
                if evidence and evidence in chunk_set:
                    edges_to_remove.append((head, tail, str(key)))
                if str(data.get("relation_type") or "") == "mentions":
                    if head == doc_node_id:
                        edges_to_remove.append((head, tail, str(key)))
                if str(data.get("relation_type") or "") == "shares_entity":
                    if head == doc_node_id or tail == doc_node_id:
                        edges_to_remove.append((head, tail, str(key)))
            for head, tail, key in edges_to_remove:
                if graph.has_edge(head, tail, key):
                    graph.remove_edge(head, tail, key)

            removed_entity_ids: list[str] = []
            for node_id in list(graph.nodes()):
                data = graph.nodes[node_id]
                doc_ids = set(data.get("doc_ids", []))
                if doc_id not in doc_ids:
                    continue
                doc_ids.discard(doc_id)
                source_chunks = set(data.get("source_chunk_ids", []))
                if chunk_set:
                    source_chunks -= chunk_set
                if not doc_ids:
                    graph.remove_node(node_id)
                    removed_entity_ids.append(node_id)
                else:
                    graph.nodes[node_id]["doc_ids"] = sorted(doc_ids)
                    graph.nodes[node_id]["source_chunk_ids"] = sorted(source_chunks)
                    if graph.nodes[node_id].get("parent_doc_id") == doc_id:
                        graph.nodes[node_id]["parent_doc_id"] = None

            dangling: list[tuple[str, str, str]] = []
            for head, tail, key in graph.edges(keys=True):
                if head not in graph or tail not in graph:
                    dangling.append((head, tail, str(key)))
            for head, tail, key in dangling:
                if graph.has_edge(head, tail, key):
                    graph.remove_edge(head, tail, key)
            if doc_node_id in graph.nodes:
                graph.remove_node(doc_node_id)

            return removed_entity_ids

    def _select_node_pool(
        self,
        *,
        graph: nx.MultiDiGraph,
        doc_id: str | None,
        min_mentions: int = 2,
        filter_noise: bool = False,
        pool_limit: int = 200,
    ) -> list[str]:
        """候选节点池：按 mention 排序，供连通子图导出使用。"""
        node_ids = list(graph.nodes())
        # 兼容旧测试/旧图：未标 layer 但有 entity_type 的节点按 L1 处理；
        # 明确标了 layer='L0' 的文档节点才排除。
        node_ids = [
            node_id
            for node_id in node_ids
            if str(graph.nodes[node_id].get("layer") or "L1") != "L0"
        ]
        if doc_id:
            node_ids = [node_id for node_id in node_ids if doc_id in set(graph.nodes[node_id].get("doc_ids", []))]
        if filter_noise:
            node_ids = [
                node_id
                for node_id in node_ids
                if is_meaningful_graph_label(str(graph.nodes[node_id].get("label") or node_id))
            ]
        if min_mentions > 1:
            node_ids = [
                node_id
                for node_id in node_ids
                if int(graph.nodes[node_id].get("mention_count") or 0) >= min_mentions
            ]
        node_ids.sort(key=lambda node_id: int(graph.nodes[node_id].get("mention_count") or 0), reverse=True)
        return node_ids[: max(pool_limit, 0)]

    def _build_connected_export(
        self,
        *,
        graph: nx.MultiDiGraph,
        pool: set[str],
        label_by_id: dict[str, str],
        relation_type: str,
        max_edges: int,
        limit_nodes: int,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """只导出参与连通的边及其端点，避免「72 孤立点 + 乱线」。

        R-020：门控放宽。共现 confidence 现在按 log(count) 累加，单次共现起步
        0.55，多次共现可超过 0.7。原阈值 0.68 直接把所有单次共现一刀切死。
        """
        min_mentions_conf = 0.55
        semantic: list[dict[str, Any]] = []
        mentions: list[dict[str, Any]] = []

        for head, tail, key, data in graph.edges(keys=True, data=True):
            if head not in pool or tail not in pool:
                continue
            rel = str(data.get("relation_type") or "mentions")
            if rel == "shares_entity":
                continue
            confidence = float(data.get("confidence") or 0.0)
            edge = {
                "id": str(data.get("edge_id") or key),
                "source": head,
                "target": tail,
                "source_label": label_by_id.get(head, head),
                "target_label": label_by_id.get(tail, tail),
                "relation_type": rel,
                "confidence": confidence,
                "evidence_chunk_id": str(data.get("evidence_chunk_id") or ""),
            }
            if rel == "mentions":
                if confidence < min_mentions_conf:
                    continue
                # R-020：不再硬性要求 technical；过滤已经在 _select_node_pool 完成
                mentions.append(edge)
            else:
                semantic.append(edge)

        mentions.sort(key=lambda item: item["confidence"], reverse=True)
        semantic.sort(key=lambda item: item["confidence"], reverse=True)

        if relation_type == "all":
            selected_edges = (semantic + mentions)[:max_edges]
        elif relation_type == "semantic":
            selected_edges = semantic[:max_edges]
        elif relation_type == "mentions":
            selected_edges = mentions[:max_edges]
        else:
            selected_edges = list(semantic)
            for edge in mentions:
                if len(selected_edges) >= max_edges:
                    break
                selected_edges.append(edge)

        node_ids = {edge["source"] for edge in selected_edges} | {edge["target"] for edge in selected_edges}
        if not node_ids:
            return [], []

        trimmed_ids = self._trim_nodes_to_limit(
            graph=graph,
            node_ids=node_ids,
            edges=selected_edges,
            limit_nodes=limit_nodes,
        )
        trimmed_set = set(trimmed_ids)
        trimmed_edges = [
            edge
            for edge in selected_edges
            if edge["source"] in trimmed_set and edge["target"] in trimmed_set
        ]
        connected_ids = {edge["source"] for edge in trimmed_edges} | {edge["target"] for edge in trimmed_edges}
        trimmed_ids = [node_id for node_id in trimmed_ids if node_id in connected_ids]
        trimmed_ids = self._sort_nodes_by_importance(graph, trimmed_ids, trimmed_edges)
        return trimmed_ids, trimmed_edges

    def _trim_nodes_to_limit(
        self,
        *,
        graph: nx.MultiDiGraph,
        node_ids: set[str],
        edges: list[dict[str, Any]],
        limit_nodes: int,
    ) -> list[str]:
        if len(node_ids) <= limit_nodes:
            return list(node_ids)

        degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
        for edge in edges:
            degree[edge["source"]] += 1
            degree[edge["target"]] += 1

        remaining = set(node_ids)
        while len(remaining) > limit_nodes:
            candidates = list(remaining)
            candidates.sort(
                key=lambda node_id: (
                    degree.get(node_id, 0),
                    int(graph.nodes[node_id].get("mention_count") or 0),
                )
            )
            remove_id = candidates[0]
            remaining.remove(remove_id)
            for edge in edges:
                if edge["source"] == remove_id or edge["target"] == remove_id:
                    other = edge["target"] if edge["source"] == remove_id else edge["source"]
                    if other in remaining:
                        degree[other] = max(0, degree.get(other, 0) - 1)
        return list(remaining)

    def _sort_nodes_by_importance(
        self,
        graph: nx.MultiDiGraph,
        node_ids: list[str],
        edges: list[dict[str, Any]],
    ) -> list[str]:
        degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
        for edge in edges:
            if edge["source"] in degree:
                degree[edge["source"]] += 1
            if edge["target"] in degree:
                degree[edge["target"]] += 1
        return sorted(
            node_ids,
            key=lambda node_id: (
                degree.get(node_id, 0),
                int(graph.nodes[node_id].get("mention_count") or 0),
            ),
            reverse=True,
        )

    def _ensure_parent_dir(self) -> None:
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)

    def _require_graph(self) -> nx.MultiDiGraph:
        if self._graph is None:
            self._graph = nx.MultiDiGraph()
        return self._graph
