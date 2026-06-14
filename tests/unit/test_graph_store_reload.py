"""GraphStore 磁盘热加载测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import networkx as nx
import pytest

from stream_kg.kg.graph_store import GraphStore


@pytest.mark.asyncio
async def test_export_reloads_updated_graph_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "graph.pkl"
    reader = GraphStore(str(path))
    await reader.initialize()
    exported_empty = await reader.export_graph()
    assert exported_empty["stats"]["node_count"] == 0

    writer = GraphStore(str(path))
    graph = nx.MultiDiGraph()
    # R-015 后导出仅保留连通子图，构造一对带边的节点确保热加载结果可观测
    graph.add_node("n1", label="Redis", entity_type="method", mention_count=3, doc_ids=["d1"])
    graph.add_node("n2", label="BERT", entity_type="method", mention_count=2, doc_ids=["d1"])
    graph.add_edge(
        "n1",
        "n2",
        key="edge-1",
        edge_id="edge-1",
        relation_type="mentions",
        confidence=0.82,
        evidence_chunk_id="chunk-1",
        created_at=datetime.now(UTC),
    )
    writer._graph = graph  # noqa: SLF001
    await writer.persist()

    exported_after = await reader.export_graph(min_mentions=1, relation_type="all")
    node_ids = {node["id"] for node in exported_after["nodes"]}
    assert "n1" in node_ids
    assert "n2" in node_ids
