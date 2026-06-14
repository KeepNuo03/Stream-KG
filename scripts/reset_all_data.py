# -*- coding: utf-8 -*-
# DANGER: wipes all ingested data and forces re-creation of Qdrant collections
# with the correct COSINE distance.
#
# Usage:
#   1) stop backend (Ctrl+C in its terminal)
#   2) & "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/reset_all_data.py --yes
#   3) restart backend
#   4) re-upload documents from frontend

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import QdrantClient

from stream_kg.config import settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="confirm destructive operation")
    args = parser.parse_args()

    print("=" * 60)
    print("This will permanently delete:")
    print("  - Qdrant collection:", settings.qdrant_collection_chunks)
    print("  - Qdrant collection:", settings.qdrant_collection_entities)
    print("  - SQLite file       :", settings.sqlite_path)
    print("  - Graph file        :", settings.graph_path)
    print()
    if not args.yes:
        print("Re-run with --yes to confirm.")
        return 1

    # 1) Qdrant
    print("[1/3] dropping Qdrant collections ...")
    try:
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        for name in (settings.qdrant_collection_chunks, settings.qdrant_collection_entities):
            try:
                client.delete_collection(name)
                print("  dropped", name)
            except Exception as exc:
                print("  skip   ", name, " (", type(exc).__name__, str(exc)[:60], ")")
    except Exception as exc:
        print("  Qdrant client error:", type(exc).__name__, str(exc))

    # 2) SQLite
    print("[2/3] removing SQLite ...")
    p = Path(settings.sqlite_path)
    if p.exists():
        p.unlink()
        print("  removed", p)
    else:
        print("  not found", p)

    # 3) Graph
    print("[3/3] removing graph pickle ...")
    p = Path(settings.graph_path)
    if p.exists():
        p.unlink()
        print("  removed", p)
    else:
        print("  not found", p)

    print()
    print("DONE. Now:")
    print("  1) restart backend so it recreates collections with COSINE distance")
    print("  2) re-upload your PDFs from the frontend")
    print("  3) run: & \"$env:USERPROFILE\\.local\\bin\\uv.exe\" run python scripts/diagnose_rag.py")
    print("     expect [5c] self-retrieval top_score >= 0.95")
    return 0


if __name__ == "__main__":
    sys.exit(main())
