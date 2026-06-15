"""Knowledge graph endpoints."""

from fastapi import HTTPException
from fastapi import APIRouter

from api.dependencies import get_graph_store
from stream_kg.config import settings

router = APIRouter()


@router.get("")
async def get_graph(
    doc_id: str | None = None,
    view_mode: str = "mixed",
    limit_nodes: int = 36,
    min_mentions: int = 2,
    relation_type: str | None = "balanced",
    max_edges: int = 48,
) -> dict:
    """Return graph nodes and edges. P1: empty when FEATURE_KG_ENABLED=false."""
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
