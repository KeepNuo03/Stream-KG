"""跨文档冲突检测：LLM pairwise judge。"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any
from uuid import uuid4

from stream_kg.kg.graph_store import GraphStore
from stream_kg.llm.deepseek_client import DeepSeekClient
from stream_kg.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

CONFLICT_PROMPT = """你是知识一致性审查员。判断以下两条陈述是否冲突（结论相反或不可同时成立）。

输出严格 JSON：
{"is_conflict": bool, "confidence": 0-1, "reason": "一句话"}

陈述 A（文档 {doc_a}）：
{evidence_a}

陈述 B（文档 {doc_b}）：
{evidence_b}
"""


class ConflictDetector:
    """扫描跨文档 evidence 对，调用 LLM 判断是否冲突。"""

    def __init__(
        self,
        *,
        sqlite_store: SQLiteStore,
        graph_store: GraphStore,
        llm_client: DeepSeekClient | None = None,
    ) -> None:
        self.sqlite_store = sqlite_store
        self.graph_store = graph_store
        self.llm_client = llm_client or DeepSeekClient()

    async def scan_after_ingest(self, *, doc_id: str) -> int:
        """对新摄入文档相关的跨文档 evidence 对做冲突检测。"""
        await self.graph_store.initialize()
        graph = self.graph_store._require_graph()  # noqa: SLF001

        pairs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for head, tail, _key, data in graph.edges(keys=True, data=True):
            rel = str(data.get("relation_type") or "")
            if rel in {"mentions", "shares_entity", "co_occurs"}:
                continue
            evidence = str(data.get("evidence") or "").strip()
            chunk_id = str(data.get("evidence_chunk_id") or "")
            if not evidence:
                continue
            head_docs = set(graph.nodes.get(head, {}).get("doc_ids") or [])
            tail_docs = set(graph.nodes.get(tail, {}).get("doc_ids") or [])
            edge_docs = head_docs | tail_docs
            if doc_id not in edge_docs:
                continue
            other_docs = sorted(d for d in edge_docs if d != doc_id)
            if not other_docs:
                continue
            pair_key = tuple(sorted((head, tail)))
            pairs[pair_key].append(
                {
                    "head": head,
                    "tail": tail,
                    "relation_type": rel,
                    "evidence": evidence,
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                }
            )

        created = 0
        for (_h, _t), items in pairs.items():
            if len(items) < 2:
                continue
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    if a["doc_id"] == b["doc_id"]:
                        continue
                    conflict = await self._judge_pair(a, b)
                    if not conflict:
                        continue
                    conflict_id = str(uuid4())
                    await self.sqlite_store.insert_kg_conflict(
                        conflict_id=conflict_id,
                        head_id=a["head"],
                        tail_id=a["tail"],
                        evidence_a=a["evidence"],
                        evidence_b=b["evidence"],
                        doc_a=a["doc_id"],
                        doc_b=b["doc_id"],
                        llm_judgment=conflict["reason"],
                        confidence=float(conflict["confidence"]),
                    )
                    await self.graph_store.upsert_entity_edge_v2(
                        head_entity_id=a["head"],
                        tail_entity_id=a["tail"],
                        relation_type="conflict",
                        confidence=float(conflict["confidence"]),
                        evidence=f"{a['evidence']} || {b['evidence']}",
                        evidence_chunk_id=a.get("chunk_id") or b.get("chunk_id"),
                    )
                    await self.sqlite_store.insert_kg_change_event(
                        event_id=str(uuid4()),
                        event_type="conflict",
                        head_id=a["head"],
                        tail_id=a["tail"],
                        relation_type="conflict",
                        evidence=conflict["reason"],
                        doc_id=doc_id,
                        payload={
                            "conflict_id": conflict_id,
                            "doc_a": a["doc_id"],
                            "doc_b": b["doc_id"],
                        },
                    )
                    created += 1
        if created:
            await self.graph_store.persist()
        return created

    async def _judge_pair(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
        prompt = CONFLICT_PROMPT.format(
            doc_a=a["doc_id"],
            doc_b=b["doc_id"],
            evidence_a=a["evidence"],
            evidence_b=b["evidence"],
        )
        try:
            raw = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=256,
            )
            parsed = json.loads(raw.strip().strip("`").removeprefix("json"))
            if not parsed.get("is_conflict"):
                return None
            confidence = float(parsed.get("confidence") or 0.0)
            if confidence < 0.7:
                return None
            return {"confidence": confidence, "reason": str(parsed.get("reason") or "")}
        except Exception as exc:
            logger.warning("Conflict judge failed: %s", exc)
            return None
