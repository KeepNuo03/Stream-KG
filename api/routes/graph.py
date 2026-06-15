"""Knowledge graph endpoints."""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.dependencies import get_graph_store, get_sqlite_store
from stream_kg.config import settings
from stream_kg.kg.labels import localize_relation
from stream_kg.llm.deepseek_client import DeepSeekClient, DeepSeekError

logger = logging.getLogger(__name__)

router = APIRouter()

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "edge_explain.txt"


def _collect_edge_evidence(
    graph,
    head_id: str,
    tail_id: str,
    relation_type: str,
) -> tuple[list[str], str]:
    """从图中收集边证据（双向查找，兼容存储方向不一致）。"""
    evidence_chunks: list[str] = []
    evidence_text = ""
    for h, t in ((head_id, tail_id), (tail_id, head_id)):
        for _k, data in graph.get_edge_data(h, t, default={}).items():
            if str(data.get("relation_type") or "") != relation_type:
                continue
            chunk_id = str(data.get("evidence_chunk_id") or "")
            if chunk_id and chunk_id not in evidence_chunks:
                evidence_chunks.append(chunk_id)
            ev = str(data.get("evidence") or "").strip()
            if ev and not evidence_text:
                evidence_text = ev
    return evidence_chunks, evidence_text


def _fallback_explanation(
    head_label: str,
    tail_label: str,
    relation_label: str,
    evidence_text: str,
) -> str:
    if evidence_text.strip():
        snippet = evidence_text.strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        return (
            f"「{head_label}」与「{tail_label}」存在「{relation_label}」关系。"
            f"依据原文：{snippet}"
        )
    return (
        f"「{head_label}」与「{tail_label}」在知识图谱中被标注为「{relation_label}」关系。"
        "可点击实体查看引用片段以核对依据。"
    )


class EdgeExplainRequest(BaseModel):
    head_id: str
    tail_id: str
    relation_type: str
    evidence: str = ""
    head_label: str = ""
    tail_label: str = ""


@router.get("")
async def get_graph(
    doc_id: str | None = None,
    focus_doc_id: str | None = None,
    focus_node_id: str | None = None,
    hop: int = 1,
    view_mode: str = "mixed",
    limit_nodes: int = 36,
    min_mentions: int = 2,
    relation_type: str | None = "balanced",
    max_edges: int = 48,
) -> dict:
    """Return graph nodes and edges."""
    if not settings.feature_kg_enabled:
        return {
            "nodes": [],
            "edges": [],
            "stats": {"node_count": 0, "edge_count": 0},
            "placeholder": True,
        }
    graph_store = get_graph_store()
    await graph_store.initialize()
    return await graph_store.export_graph(
        doc_id=doc_id,
        focus_doc_id=focus_doc_id,
        focus_node_id=focus_node_id,
        hop=hop,
        view_mode=view_mode,
        limit_nodes=min(limit_nodes, 96),
        min_mentions=max(min_mentions, 1),
        relation_type=relation_type,
        filter_noise=True,
        max_edges=min(max_edges, 120),
    )


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str) -> dict:
    """Entity detail with mentions and relations."""
    if not settings.feature_kg_enabled:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    graph_store = get_graph_store()
    await graph_store.initialize()
    detail = await graph_store.get_entity_detail(entity_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    return detail


@router.post("/edges/explain")
async def explain_edge(body: EdgeExplainRequest) -> dict:
    if not settings.feature_kg_enabled:
        raise HTTPException(status_code=404, detail="Graph feature disabled")

    edge_key = f"{body.head_id}|{body.tail_id}|{body.relation_type}"
    sqlite_store = get_sqlite_store()
    cached = await sqlite_store.get_edge_explanation(edge_key)
    if cached:
        return cached

    graph_store = get_graph_store()
    await graph_store.initialize()
    head_label = body.head_label
    tail_label = body.tail_label
    graph = graph_store._require_graph()  # noqa: SLF001
    if head_label == "" and body.head_id in graph.nodes:
        head_label = str(graph.nodes[body.head_id].get("label") or body.head_id)
    if tail_label == "" and body.tail_id in graph.nodes:
        tail_label = str(graph.nodes[body.tail_id].get("label") or body.tail_id)

    relation_label = localize_relation(body.relation_type)
    evidence_chunks, graph_evidence = _collect_edge_evidence(
        graph, body.head_id, body.tail_id, body.relation_type
    )
    evidence_text = (body.evidence or graph_evidence).strip()
    if not evidence_text and evidence_chunks:
        chunks_map = await sqlite_store.get_chunks_by_ids(evidence_chunks[:3])
        chunk_texts = [
            str(record.content or "").strip()
            for record in chunks_map.values()
            if str(record.content or "").strip()
        ]
        if chunk_texts:
            evidence_text = "\n".join(chunk_texts)

    client = DeepSeekClient()
    if not client.is_ready:
        explanation = _fallback_explanation(
            head_label, tail_label, relation_label, evidence_text
        )
    else:
        template = PROMPT_PATH.read_text(encoding="utf-8")
        prompt = (
            template.replace("{head_label}", head_label)
            .replace("{tail_label}", tail_label)
            .replace("{relation_label}", relation_label)
            .replace("{relation_type}", relation_label)
            .replace("{evidence}", evidence_text or "（无显式证据）")
        )
        try:
            explanation = await client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=256,
            )
        except DeepSeekError as exc:
            logger.warning("edge explain LLM failed: %s", exc)
            explanation = _fallback_explanation(
                head_label, tail_label, relation_label, evidence_text
            )

    await sqlite_store.upsert_edge_explanation(
        edge_key=edge_key,
        head_id=body.head_id,
        tail_id=body.tail_id,
        relation_type=body.relation_type,
        explanation=explanation.strip(),
        evidence_chunks=evidence_chunks,
    )
    saved = await sqlite_store.get_edge_explanation(edge_key)
    return saved or {
        "edge_key": edge_key,
        "explanation": explanation.strip(),
        "evidence_chunks": evidence_chunks,
    }


@router.get("/conflicts/{conflict_id}")
async def get_conflict(conflict_id: str) -> dict:
    sqlite_store = get_sqlite_store()
    conflict = await sqlite_store.get_kg_conflict(conflict_id)
    if conflict is None:
        raise HTTPException(status_code=404, detail=f"Conflict {conflict_id} not found")
    return conflict
