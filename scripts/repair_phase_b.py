r"""Repair Phase B SQLite sync from graph.pkl (avoid re-running LLM extraction).

将 graph.pkl 中 LLM 抽取的 L1 实体与 L0-L1 边补写到 SQLite：
- entities
- doc_entity_links

用法：
    & "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/repair_phase_b.py
"""

from __future__ import annotations

import asyncio
import pickle
import sys
from pathlib import Path

import networkx as nx

from stream_kg.config import settings
from stream_kg.storage.sqlite_store import SQLiteStore

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


async def main() -> int:
    graph_path = Path(settings.graph_path)
    if not graph_path.exists():
        print(f"[WARN] graph file not found: {graph_path}")
        return 1

    with graph_path.open("rb") as fp:
        graph: nx.MultiDiGraph = pickle.load(fp)

    store = SQLiteStore(settings.sqlite_path)
    await store.initialize()

    entity_count = 0
    link_count = 0

    for node_id, data in graph.nodes(data=True):
        if data.get("layer") != "L1":
            continue
        entity_id = str(data.get("id") or node_id)
        canonical_name = str(data.get("label") or entity_id)
        entity_type = str(data.get("entity_type") or "concept")
        aliases = list(data.get("aliases") or [])
        description = str(data.get("description") or "") or None
        salience = float(data.get("salience") or 0.5)
        doc_ids = list(data.get("doc_ids") or [])

        await store.upsert_entity(
            entity_id=entity_id,
            canonical_name=canonical_name,
            entity_type=entity_type,
            aliases=aliases,
            description=description,
            salience=salience,
            embedding_id=entity_id,
        )
        entity_count += 1

        for doc_id in doc_ids:
            await store.upsert_doc_entity_link(
                doc_id=str(doc_id),
                entity_id=entity_id,
                mention_count_delta=int(data.get("mention_count") or 1),
                salience=salience,
            )
            link_count += 1

    print(f"[OK] repaired entities={entity_count}, doc_entity_links={link_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
