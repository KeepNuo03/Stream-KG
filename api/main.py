"""FastAPI 应用入口。

职责：
- 初始化日志；
- 注册 CORS 与业务路由；
- 在 startup 阶段初始化 SQLite 与 Qdrant。
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import get_embedder, get_qdrant_store, get_sqlite_store
from api.routes import chat, documents, graph, mindmap, system
from stream_kg.config import settings
from stream_kg.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="stream-kg", version=settings.app_version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router, prefix="/api/v1", tags=["system"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(mindmap.router, prefix="/api/v1/documents", tags=["mindmap"])
app.include_router(graph.router, prefix="/api/v1/graph", tags=["graph"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])


@app.on_event("startup")
async def startup_event() -> None:
    """启动钩子：初始化持久化依赖。"""
    sqlite_store = get_sqlite_store()
    await sqlite_store.initialize()

    qdrant_store = get_qdrant_store()
    try:
        qdrant_store.initialize()
    except Exception as exc:
        # Qdrant 在本地未启动时不阻塞服务启动，便于先调试非向量链路。
        logger.warning("Qdrant initialize failed on startup: %s", exc)

    embedder = get_embedder()
    # 模型首次下载可能较慢，启动探活给足时间。
    embedder.timeout_sec = max(settings.embedding_request_timeout_sec, 30.0)
    embed_status = await embedder.probe_backend()
    logger.info(
        "Startup flags: FEATURE_KG_ENABLED=%s embedding=%s",
        settings.feature_kg_enabled,
        embed_status,
    )
    if embed_status.get("mode") in {"pseudo", "client_pseudo", "down", "unknown"}:
        logger.warning(
            "Embedding 未使用真实向量（mode=%s）。请先启动 embedding_server，并对已导入文档执行 reprocess。",
            embed_status.get("mode"),
        )
