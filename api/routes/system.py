"""系统级路由：健康检查与统计信息。"""

import asyncio

import httpx
from fastapi import APIRouter

from api.dependencies import get_embedder, get_qdrant_store, get_sqlite_store
from api.schemas import HealthResponse
from stream_kg.config import settings

router = APIRouter()


async def _probe_http_health(url: str) -> dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return {"mode": "down", "device": ""}
            data = resp.json()
            return {
                "mode": str(data.get("mode") or "unknown"),
                "device": str(data.get("device") or ""),
            }
    except Exception:
        return {"mode": "down", "device": ""}


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """健康检查。

    当前检查范围：
    - SQLite 初始化可用；
    - Qdrant 可连接；
    - embedding/llm 先返回 unknown（后续可补真实探活）。
    """
    sqlite_store = get_sqlite_store()
    await sqlite_store.initialize()

    qdrant_store = get_qdrant_store()
    qdrant_ok = qdrant_store.health()
    embedder = get_embedder()
    embed_status = await embedder.probe_backend()
    embedding_label = (
        f"{embed_status.get('status')}:{embed_status.get('mode')}"
        if embed_status.get("status") == "ok"
        else "down"
    )

    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        services={
            "qdrant": "ok" if qdrant_ok else "down",
            "sqlite": "ok",
            "embedding_server": embedding_label,
            "llm_api": "configured" if settings.llm_api_key else "missing_key",
            "feature_kg_enabled": "true" if settings.feature_kg_enabled else "false",
        },
        gpu={"available": False, "vram_used_mb": 0, "vram_total_mb": 12288},
    )


@router.get("/status")
async def status() -> dict[str, object]:
    """轻量服务状态：embedding / reranker 的 mode 与 device。

    用于前端顶栏状态指示灯；与 /health 区别：本接口不依赖 SQLite/Qdrant，
    专门探活两个本地模型 server。

    探活策略：永远尝试探活两个 server，让 UI 反映"真实服务状态"；
    `feature_reranker_enabled` 单独透出，前端用它区分"server 起来了但 chat 链路不用"
    和"server 真没启动"两种情况。
    """
    embedding_task = _probe_http_health(settings.embedding_server_url.rstrip("/") + "/health")
    reranker_task = _probe_http_health(settings.reranker_server_url.rstrip("/") + "/health")
    embed_st, rerank_st = await asyncio.gather(embedding_task, reranker_task)
    return {
        "embedding": embed_st,
        "reranker": rerank_st,
        "feature_reranker_enabled": settings.feature_reranker_enabled,
    }


@router.get("/stats")
async def stats() -> dict[str, float | int]:
    """返回语料与会话统计（P1 最小集）。"""
    sqlite_store = get_sqlite_store()
    values = await sqlite_store.stats()
    return {
        "document_count": values["documents"],
        "chunk_count": values["chunks"],
        "entity_count": values.get("entities", 0) if settings.feature_kg_enabled else 0,
        "edge_count": values.get("edges", 0) if settings.feature_kg_enabled else 0,
        "storage_mb": 0.0,
    }
