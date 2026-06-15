"""Knowledge graph endpoints."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_graph_store, get_sqlite_store
from stream_kg.config import settings
from stream_kg.llm.deepseek_client import DeepSeekClient

router = APIRouter()

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "edge_explain.txt"


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

    template = PROMPT_PATH.read_text(encoding="utf-8")
    prompt = (
        template.replace("{head_label}", head_label)
        .replace("{tail_label}", tail_label)
        .replace("{relation_type}", body.relation_type)
        .replace("{evidence}", body.evidence or "（无显式证据）")
    )
    client = DeepSeekClient()
    explanation = await client.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=256,
    )
    evidence_chunks: list[str] = []
    for _k, data in graph.get_edge_data(body.head_id, body.tail_id, default={}).items():
        if str(data.get("relation_type") or "") == body.relation_type:
            chunk_id = str(data.get("evidence_chunk_id") or "")
            if chunk_id:
                evidence_chunks.append(chunk_id)

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
