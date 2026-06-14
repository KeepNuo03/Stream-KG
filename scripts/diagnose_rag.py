# -*- coding: utf-8 -*-
# Diagnose RAG / embedding / graph state.
# Usage:
#   & "$env:USERPROFILE\.local\bin\uv.exe" run python scripts/diagnose_rag.py

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from stream_kg.config import settings
from stream_kg.encoding.embedder import Embedder
from stream_kg.kg.graph_store import GraphStore
from stream_kg.retrieval.vector_search import VectorSearchService
from stream_kg.storage.qdrant_store import QdrantStore
from stream_kg.storage.sqlite_store import SQLiteStore


async def main() -> None:
    print("=" * 60)
    print("[1] config thresholds (should be R-019 values)")
    print("  rag_min_relevance_score         =", settings.rag_min_relevance_score, " expect 0.20")
    print("  rag_evidence_fallback_min_score =", settings.rag_evidence_fallback_min_score, " expect 0.30")
    print("  rag_low_relevance_margin        =", settings.rag_low_relevance_margin, " expect 0.05")
    print("  feature_kg_enabled              =", settings.feature_kg_enabled, " expect True")
    print("  embedding_server_url            =", settings.embedding_server_url)
    print("  embedding_dim                   =", settings.embedding_dim)
    print("  embedding_request_timeout_sec   =", settings.embedding_request_timeout_sec, " expect 60.0")
    print("  embedding_connect_timeout_sec   =", settings.embedding_connect_timeout_sec, " expect 2.0")
    print("  embedding_fallback_cooldown_sec =", settings.embedding_fallback_cooldown_sec, " expect 5")

    print("\n" + "=" * 60)
    print("[2] Embedding server /health")
    import time as _t
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(settings.embedding_server_url + "/health")
            print("  status =", r.status_code)
            print("  body   =", r.text)
    except Exception as exc:
        print("  EMBEDDING server unreachable:", type(exc).__name__, str(exc))
        return

    print("\n[2b] Embedding /embed direct call (measure REAL inference time)")
    for sample in ["self attention", "Cheng Nuo resume objective", "knowledge graph"]:
        t0 = _t.monotonic()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    settings.embedding_server_url + "/embed",
                    json={"texts": [sample]},
                )
                dt = _t.monotonic() - t0
                payload = r.json()
                mode = payload.get("mode")
                vec = (payload.get("vectors") or [[]])[0]
                first3 = [round(x, 4) for x in vec[:3]]
                print("  [{:6.2f}s] sample={!r} mode={} first3={}".format(dt, sample, mode, first3))
        except Exception as exc:
            dt = _t.monotonic() - t0
            print("  [{:6.2f}s] sample={!r} ERR {} {}".format(dt, sample, type(exc).__name__, str(exc)[:80]))

    print("\n" + "=" * 60)
    print("[3] SQLite documents")
    sqlite = SQLiteStore(settings.sqlite_path)
    await sqlite.initialize()
    docs, total = await sqlite.list_documents(limit=20, offset=0)
    print("  total =", total)
    for d in docs:
        try:
            chunks = await sqlite.list_chunks_by_doc(d.doc_id)
            n = len(chunks)
        except Exception as exc:
            n = "ERR:" + str(exc)
        title_preview = (d.title or "")[:40]
        print("  - id=", d.doc_id[:8], " status=", d.status, " type=", d.doc_type,
              " chunks=", n, " title=", repr(title_preview))

    print("\n" + "=" * 60)
    print("[4] Qdrant chunks collection")
    qdrant = QdrantStore(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        chunks_collection=settings.qdrant_collection_chunks,
        entities_collection=settings.qdrant_collection_entities,
        vector_size=settings.embedding_dim,
    )
    try:
        qdrant.initialize()
        info = qdrant.client.get_collection(settings.qdrant_collection_chunks)
        pc = getattr(info, "points_count", None)
        ic = getattr(info, "indexed_vectors_count", None)
        print("  points_count =", pc, " indexed_vectors_count =", ic)
        # 验证 distance 到底是不是 COSINE
        try:
            vparams = info.config.params.vectors
            dist = getattr(vparams, "distance", None) or vparams
            print("  ===> collection distance =", dist, "  (must be Distance.Cosine)")
        except Exception as inner:
            print("  distance probe err:", inner)
    except Exception as exc:
        print("  Qdrant query failed:", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[5] real vector_search top_score (CORE EVIDENCE)")
    embedder = Embedder(server_url=settings.embedding_server_url, dim=settings.embedding_dim)
    vs = VectorSearchService(sqlite_store=sqlite, qdrant_store=qdrant, embedder=embedder)
    test_queries = [
        "Transformer self attention computation",
        "self attention",
        "attention is all you need",
        "Cheng Nuo resume objective",
        "knowledge graph",
    ]
    for q in test_queries:
        try:
            chunks = await vs.search(query=q, top_k=5)
            if not chunks:
                print("  [empty] q=", repr(q))
                continue
            top = chunks[0]
            snip = top.chunk.content[:60].replace("\n", " ")
            print("  top={:.4f} hits={} doc={} snippet={!r}  <- {!r}".format(
                top.score, len(chunks), top.chunk.doc_id[:8], snip, q
            ))
        except Exception as exc:
            print("  ERR", type(exc).__name__, str(exc), " q=", repr(q))

    print("\n" + "=" * 60)
    print("[5b] CORE: compare stored vector vs fresh embed for SAME chunk text")
    print("     (cos>=0.95 means stored is real; cos<0.2 means stored is pseudo)")
    try:
        sample_doc_id = None
        for d in docs:
            if d.status == "ready" and d.doc_type == "pdf":
                sample_doc_id = d.doc_id
                break
        if sample_doc_id is None:
            print("  no ready pdf doc to sample")
        else:
            sample_chunks = await sqlite.list_chunks_by_doc(sample_doc_id)
            for ch in sample_chunks[:3]:
                text = ch.content or ""
                if not text.strip():
                    continue
                fresh = await embedder.embed_one(text)
                from qdrant_client.http import models as qmodels
                retrieved = qdrant.client.retrieve(
                    collection_name=settings.qdrant_collection_chunks,
                    ids=[ch.chunk_id],
                    with_vectors=True,
                )
                if not retrieved:
                    print("  chunk_id={} not in Qdrant".format(ch.chunk_id[:8]))
                    continue
                stored = retrieved[0].vector
                if isinstance(stored, dict):
                    stored = next(iter(stored.values()))
                if stored is None:
                    print("  chunk_id={} has no vector in Qdrant".format(ch.chunk_id[:8]))
                    continue
                import math
                dot = sum(a * b for a, b in zip(fresh, stored))
                na = math.sqrt(sum(a * a for a in fresh))
                nb = math.sqrt(sum(b * b for b in stored))
                cos = dot / (na * nb + 1e-12)
                print("  doc={} chunk={} cos(stored, fresh)={:.4f}  text='{}...'".format(
                    sample_doc_id[:8], ch.chunk_id[:8], cos, text[:40].replace("\n", " ")
                ))
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[5c] DECISIVE: self-retrieval - use chunk text as query")
    print("     expect: top_score ~ 1.0 and top doc is itself")
    try:
        if sample_doc_id and sample_chunks:
            seed_chunk = sample_chunks[0]
            seed_text = (seed_chunk.content or "").strip()[:400]
            print("  seed doc={}  chunk={}  text='{}...'".format(
                sample_doc_id[:8], seed_chunk.chunk_id[:8], seed_text[:60].replace("\n", " ")
            ))
            hits = await vs.search(query=seed_text, top_k=3)
            for i, h in enumerate(hits):
                is_self = "*self*" if h.chunk.chunk_id == seed_chunk.chunk_id else ""
                print("    #{} top={:.4f} doc={} chunk={} {}".format(
                    i + 1, h.score, h.chunk.doc_id[:8], h.chunk.chunk_id[:8], is_self
                ))

        # also pick one chunk from attention paper
        att_doc = next((d for d in docs if "attention" in (d.title or "").lower()), None)
        if att_doc:
            att_chunks = await sqlite.list_chunks_by_doc(att_doc.doc_id)
            if att_chunks:
                # pick a non-trivial chunk (>200 chars if possible)
                seed = next((c for c in att_chunks if len(c.content or "") > 200), att_chunks[0])
                seed_text = (seed.content or "").strip()[:400]
                print("  seed doc={}  chunk={}  text='{}...'".format(
                    att_doc.doc_id[:8], seed.chunk_id[:8], seed_text[:60].replace("\n", " ")
                ))
                hits = await vs.search(query=seed_text, top_k=3)
                for i, h in enumerate(hits):
                    is_self = "*self*" if h.chunk.chunk_id == seed.chunk_id else ""
                    print("    #{} top={:.4f} doc={} chunk={} {}".format(
                        i + 1, h.score, h.chunk.doc_id[:8], h.chunk.chunk_id[:8], is_self
                    ))
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[5d] DECISIVE: bypass VectorSearchService - call Qdrant directly")
    print("     expect: same top_score as [5c] if VectorSearchService is fine")
    try:
        if sample_doc_id and sample_chunks:
            seed_chunk = sample_chunks[0]
            seed_text = (seed_chunk.content or "").strip()[:400]
            qvec = await embedder.embed_one(seed_text)
            from qdrant_client.http import models as qmodels
            raw = qdrant.client.search(
                collection_name=settings.qdrant_collection_chunks,
                query_vector=qvec,
                limit=3,
            )
            for i, r in enumerate(raw):
                print("    #{} raw_score={:.4f} id={} payload_doc_id={}".format(
                    i + 1, r.score, str(r.id)[:8],
                    str((r.payload or {}).get("doc_id") or "")[:8]
                ))
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[5e] FINAL: use STORED vector itself as query (round-trip test)")
    print("     expect: top should be itself with score=1.0")
    try:
        if sample_doc_id and sample_chunks:
            target = sample_chunks[0]
            retrieved = qdrant.client.retrieve(
                collection_name=settings.qdrant_collection_chunks,
                ids=[target.chunk_id],
                with_vectors=True,
            )
            if retrieved:
                stored_vec = retrieved[0].vector
                if isinstance(stored_vec, dict):
                    stored_vec = next(iter(stored_vec.values()))
                if stored_vec:
                    import math
                    norm = math.sqrt(sum(x * x for x in stored_vec))
                    print("  stored_vec_norm =", round(norm, 6), " dim =", len(stored_vec))
                    # query with stored vector directly
                    hits = qdrant.client.search(
                        collection_name=settings.qdrant_collection_chunks,
                        query_vector=stored_vec,
                        limit=3,
                    )
                    print("  query=ORIGINAL stored vector of", target.chunk_id[:8])
                    for i, h in enumerate(hits):
                        mark = "*self*" if str(h.id) == target.chunk_id else ""
                        print("    #{} score={:.4f} id={} {}".format(
                            i + 1, h.score, str(h.id)[:8], mark
                        ))
                    # also try a manually normalized stored vector
                    if norm > 0:
                        n_vec = [x / norm for x in stored_vec]
                        hits2 = qdrant.client.search(
                            collection_name=settings.qdrant_collection_chunks,
                            query_vector=n_vec,
                            limit=3,
                        )
                        print("  query=MANUALLY NORMALIZED stored vector")
                        for i, h in enumerate(hits2):
                            mark = "*self*" if str(h.id) == target.chunk_id else ""
                            print("    #{} score={:.4f} id={} {}".format(
                                i + 1, h.score, str(h.id)[:8], mark
                            ))
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[5f] FINAL-2: embedder consistency (same text -> same vector?)")
    print("     and: use fresh vec directly as search query")
    try:
        if sample_doc_id and sample_chunks:
            target = sample_chunks[0]
            text = (target.content or "").strip()[:400]
            # 5 consecutive embeds of identical text
            import math
            vecs = []
            for i in range(5):
                v = await embedder.embed_one(text)
                norm = math.sqrt(sum(x * x for x in v))
                vecs.append(v)
                print("  embed #{} norm={:.6f} first3={} backend_mode={}".format(
                    i + 1, norm,
                    [round(x, 4) for x in v[:3]],
                    getattr(embedder, "_last_backend_mode", "?"),
                ))
            # pairwise cos
            def cos(a, b):
                dot = sum(x * y for x, y in zip(a, b))
                na = math.sqrt(sum(x * x for x in a))
                nb = math.sqrt(sum(y * y for y in b))
                return dot / (na * nb + 1e-12)
            for i in range(1, len(vecs)):
                print("  cos(#1, #{}) = {:.6f}".format(i + 1, cos(vecs[0], vecs[i])))

            # Use one of the fresh vec to search Qdrant
            hits = qdrant.client.search(
                collection_name=settings.qdrant_collection_chunks,
                query_vector=vecs[0],
                limit=3,
            )
            print("  search with embed #1:")
            for i, h in enumerate(hits):
                mark = "*self*" if str(h.id) == target.chunk_id else ""
                print("    #{} score={:.4f} id={} {}".format(
                    i + 1, h.score, str(h.id)[:8], mark
                ))

            # compare with stored
            retrieved = qdrant.client.retrieve(
                collection_name=settings.qdrant_collection_chunks,
                ids=[target.chunk_id],
                with_vectors=True,
            )
            if retrieved:
                stored_v = retrieved[0].vector
                if isinstance(stored_v, dict):
                    stored_v = next(iter(stored_v.values()))
                print("  cos(fresh#1, stored) = {:.6f}".format(cos(vecs[0], stored_v)))
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))

    print("\n" + "=" * 60)
    print("[6] graph internal state")
    gs = GraphStore(settings.graph_path)
    await gs.initialize()
    g = gs._graph  # type: ignore[attr-defined]
    if g is None:
        print("  graph is None")
    else:
        print("  nodes =", g.number_of_nodes(), " edges =", g.number_of_edges())
        if g.number_of_nodes():
            pairs = sorted(
                ((n, int(g.nodes[n].get("mention_count") or 0)) for n in g.nodes()),
                key=lambda x: x[1], reverse=True
            )[:10]
            print("  top10 nodes (label, mentions):")
            for nid, mc in pairs:
                label = g.nodes[nid].get("label")
                print("    ", repr(str(label)[:40]), " mentions=", mc)

    print("\n" + "=" * 60)
    print("[7] export_graph (what frontend gets)")
    try:
        result = await gs.export_graph()
        print("  nodes=", len(result["nodes"]), " edges=", len(result["edges"]),
              " stats=", result["stats"])
    except Exception as exc:
        print("  ERR", type(exc).__name__, str(exc))


if __name__ == "__main__":
    asyncio.run(main())
