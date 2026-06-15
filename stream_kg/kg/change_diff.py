"""摄入前后图谱变更 diff，用于增量提醒 Toast。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import networkx as nx

from stream_kg.storage.sqlite_store import SQLiteStore

TRACKED_RELATIONS = {"extends", "improves", "shares_entity", "contradicts", "conflict"}


def snapshot_graph_edges(graph: nx.MultiDiGraph) -> set[str]:
    """序列化边集合，用于前后 diff。"""
    keys: set[str] = set()
    for head, tail, _key, data in graph.edges(keys=True, data=True):
        rel = str(data.get("relation_type") or "mentions")
        if rel not in TRACKED_RELATIONS:
            continue
        keys.add(f"{head}|{tail}|{rel}")
    return keys


def diff_edge_snapshots(before: set[str], after: set[str]) -> list[dict[str, str]]:
    """返回新增边的结构化列表。"""
    added = sorted(after - before)
    events: list[dict[str, str]] = []
    for item in added:
        head, tail, rel = item.split("|", 2)
        events.append({"head_id": head, "tail_id": tail, "relation_type": rel})
    return events


async def persist_new_edge_events(
    *,
    sqlite_store: SQLiteStore,
    graph: nx.MultiDiGraph,
    new_edges: list[dict[str, str]],
    doc_id: str | None = None,
) -> int:
    """将新增边写入 kg_change_events。"""
    count = 0
    for edge in new_edges:
        head = edge["head_id"]
        tail = edge["tail_id"]
        rel = edge["relation_type"]
        evidence = ""
        if graph.has_edge(head, tail):
            for _k, data in graph.get_edge_data(head, tail).items():
                if str(data.get("relation_type") or "") == rel:
                    evidence = str(data.get("evidence") or "")
                    break
        event_type = "conflict" if rel == "conflict" else "new_cross_edge"
        await sqlite_store.insert_kg_change_event(
            event_id=str(uuid4()),
            event_type=event_type,
            head_id=head,
            tail_id=tail,
            relation_type=rel,
            evidence=evidence or None,
            doc_id=doc_id,
            payload={"head_id": head, "tail_id": tail, "relation_type": rel},
        )
        count += 1
    return count
