r"""Reset KG artifacts for Phase B migration/testing.

清理范围：
- SQLite: entities / entity_mentions / temporal_edges / doc_entity_links / kg_extraction_logs
- graph.pkl

用法：
    & "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/reset_kg.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiosqlite

from stream_kg.config import settings

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


async def main() -> int:
    db_path = Path(settings.sqlite_path)
    if not db_path.exists():
        print(f"[WARN] sqlite not found: {db_path}")
    else:
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            # 注意删除顺序（先子后父）
            await db.execute("DELETE FROM doc_entity_links")
            await db.execute("DELETE FROM temporal_edges")
            await db.execute("DELETE FROM entity_mentions")
            await db.execute("DELETE FROM entities")
            await db.execute("DELETE FROM kg_extraction_logs")
            await db.commit()
        print("[OK] sqlite kg tables cleared")

    graph_path = Path(settings.graph_path)
    if graph_path.exists():
        graph_path.unlink(missing_ok=True)
        print(f"[OK] removed graph file: {graph_path}")
    else:
        print(f"[INFO] graph file not found: {graph_path}")

    print("[DONE] KG reset complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

