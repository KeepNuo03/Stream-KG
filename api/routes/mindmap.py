"""Document mindmap endpoints."""

from fastapi import APIRouter, HTTPException

from api.dependencies import get_sqlite_store
from stream_kg.kg.mindmap_service import MindmapService

router = APIRouter()


@router.get("/{doc_id}/mindmap")
async def get_document_mindmap(doc_id: str) -> dict:
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    service = MindmapService(sqlite_store=sqlite_store)
    try:
        return await service.get_or_generate(doc_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
