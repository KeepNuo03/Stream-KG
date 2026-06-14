"""文档管理路由。

本模块负责：
1) 接收 PDF 上传与 URL 导入请求；
2) 创建文档元数据并异步触发 ingestion 流水线；
3) 提供文档列表、详情、重处理、删除、原文件下载接口。

设计要点：
- 上传接口返回 202，前端通过轮询文档状态观察处理进度；
- 删除时同时清理向量、元数据与本地文件，避免脏数据残留；
- URL 导入做基础 SSRF 防护，拒绝内网地址。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from api.dependencies import get_ingest_pipeline, get_kg_cleanup_service, get_qdrant_store, get_sqlite_store
from api.schemas import (
    BatchDeleteRequest,
    BatchDeleteResponse,
    DocumentListResponse,
    DocumentQueuedResponse,
    DocumentSummary,
    ImportUrlRequest,
)
from stream_kg.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _validate_url(url: str) -> None:
    """校验 URL 安全性，阻止内网探测类 SSRF。

    校验规则：
    - 仅允许 http/https；
    - 禁止 localhost / 127.0.0.1；
    - 解析域名后若落入私网、回环、链路本地网段则拒绝。
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Only http/https URLs are allowed")
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid URL host")
    if hostname in {"localhost", "127.0.0.1"}:
        raise HTTPException(status_code=400, detail="Localhost URL is not allowed")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(hostname))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to resolve host: {exc}") from exc
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise HTTPException(status_code=400, detail="Private network URL is not allowed")


async def _enqueue_ingest(doc_id: str) -> None:
    """后台任务入口：按文档 ID 执行 ingestion 流水线。"""
    pipeline = get_ingest_pipeline()
    await pipeline.run(doc_id)


def _delete_vectors_best_effort(doc_id: str) -> None:
    """后台最佳努力删除向量，不阻塞主请求。"""
    qdrant_store = get_qdrant_store()
    try:
        qdrant_store.delete_chunks_by_doc(doc_id)
    except Exception as exc:
        logger.warning("Best-effort vector delete failed for %s: %s", doc_id, exc)


def _error_code_from_status(status_code: int) -> str:
    if status_code == 404:
        return "DOCUMENT_NOT_FOUND"
    if status_code == 409:
        return "DOCUMENT_PROCESSING"
    return "DOCUMENT_DELETE_FAILED"


async def _delete_document_with_cascade(doc_id: str, *, background_tasks: BackgroundTasks | None = None) -> None:
    """删除单文档及其关联资源（供单删/批删复用）。"""
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if document.status == "processing":
        raise HTTPException(status_code=409, detail="Document is currently processing")

    if settings.feature_kg_enabled:
        kg_cleanup = get_kg_cleanup_service()
        await kg_cleanup.cleanup_document(doc_id)
    else:
        await sqlite_store.delete_document(doc_id)

    # 向量清理放到后台，避免删除接口被 Qdrant 慢请求阻塞。
    if background_tasks is not None:
        background_tasks.add_task(_delete_vectors_best_effort, doc_id)
    else:
        await asyncio.to_thread(_delete_vectors_best_effort, doc_id)

    # 文件系统清理：即使失败也不影响主删除流程结果。
    if document.doc_type == "pdf":
        file_path = Path(document.source_uri)
        if file_path.exists():
            file_path.unlink(missing_ok=True)
    parsed_dir = Path(settings.mineru_output_dir) / Path(document.source_uri).stem
    if parsed_dir.exists() and parsed_dir.is_dir():
        shutil.rmtree(parsed_dir, ignore_errors=True)


@router.post("/upload", status_code=202, response_model=DocumentQueuedResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str | None = None,
) -> DocumentQueuedResponse:
    """上传 PDF 并进入异步处理队列。

    处理步骤：
    1) 校验扩展名与文件大小；
    2) 保存到本地 uploads 目录；
    3) 在 SQLite 创建一条 pending 文档记录；
    4) 注册 FastAPI BackgroundTasks 异步入库任务。
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only .pdf files are supported")

    # 注意：MVP 阶段直接读入内存，50MB 上限可接受。
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File exceeds 50MB limit")

    doc_id = str(uuid4())
    # 统一使用 doc_id 作为文件名，避免同名覆盖和路径注入。
    upload_dir = Path(settings.data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{doc_id}.pdf"
    file_path.write_bytes(content)

    # 先写元数据，再触发异步任务，保证前端可立即轮询到 pending 状态。
    sqlite_store = get_sqlite_store()
    await sqlite_store.create_document(
        doc_id=doc_id,
        title=title or Path(file.filename).stem,
        doc_type="pdf",
        source_uri=str(file_path),
    )
    background_tasks.add_task(_enqueue_ingest, doc_id)
    return DocumentQueuedResponse(
        doc_id=doc_id,
        status="pending",
        message="Document queued for processing",
    )


@router.post("/import-url", status_code=202, response_model=DocumentQueuedResponse)
async def import_url(
    background_tasks: BackgroundTasks,
    body: ImportUrlRequest,
) -> DocumentQueuedResponse:
    """导入网页 URL 并进入异步处理队列。"""
    _validate_url(str(body.url))

    doc_id = str(uuid4())
    sqlite_store = get_sqlite_store()
    await sqlite_store.create_document(
        doc_id=doc_id,
        title=body.title or body.url.host or "untitled-web-document",
        doc_type="web",
        source_uri=str(body.url),
    )
    background_tasks.add_task(_enqueue_ingest, doc_id)
    return DocumentQueuedResponse(
        doc_id=doc_id,
        status="pending",
        message="Document queued for processing",
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DocumentListResponse:
    """分页列出文档。

    说明：
    - `chunk_count` 通过按 doc_id 查询 chunks 计算；
    - `entity_count` 在 P1 固定为 0（P2 图谱上线后再补）。
    """
    sqlite_store = get_sqlite_store()
    documents, total = await sqlite_store.list_documents(status=status, limit=limit, offset=offset)

    summaries: list[DocumentSummary] = []
    for document in documents:
        chunks = await sqlite_store.list_chunks_by_doc(document.doc_id)
        entity_count = (
            await sqlite_store.count_entities_by_doc(document.doc_id)
            if settings.feature_kg_enabled
            else 0
        )
        summaries.append(
            DocumentSummary(
                doc_id=document.doc_id,
                title=document.title,
                doc_type=document.doc_type,
                status=document.status,
                page_count=document.page_count,
                ingested_at=document.ingested_at,
                chunk_count=len(chunks),
                entity_count=entity_count,
                error_message=document.error_message,
            )
        )
    return DocumentListResponse(documents=summaries, total=total)


@router.get("/{doc_id}")
async def get_document(doc_id: str) -> dict:
    """获取单文档详情（含处理状态和已入库chunks）。"""
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")

    chunks = await sqlite_store.list_chunks_by_doc(doc_id)
    entities = (
        await sqlite_store.list_entities_by_doc(doc_id) if settings.feature_kg_enabled else []
    )
    return {
        "doc_id": document.doc_id,
        "title": document.title,
        "doc_type": document.doc_type,
        "status": document.status,
        "source_uri": document.source_uri,
        "published_at": document.published_at.isoformat() if document.published_at else None,
        "ingested_at": document.ingested_at.isoformat(),
        "page_count": document.page_count,
        "error_message": document.error_message,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "page_num": chunk.page_num,
                "section_title": chunk.section_title,
            }
            for chunk in chunks
        ],
        "entities": [
            {
                "entity_id": row["entity_id"],
                "canonical_name": row["canonical_name"],
                "entity_type": row["entity_type"],
            }
            for row in entities
        ],
    }


@router.get("/{doc_id}/chunks/{chunk_id}")
async def get_chunk(doc_id: str, chunk_id: str) -> dict:
    """返回某文档下单个 chunk 的完整内容，供前端引用展开。"""
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    chunk_map = await sqlite_store.get_chunks_by_ids([chunk_id])
    chunk = chunk_map.get(chunk_id)
    if chunk is None or chunk.doc_id != doc_id:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_title": document.title,
        "doc_type": document.doc_type,
        "content": chunk.content,
        "page_num": chunk.page_num,
        "section_title": chunk.section_title,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "chunk_type": chunk.chunk_type,
    }


@router.get("/{doc_id}/file")
async def get_document_file(doc_id: str) -> FileResponse:
    """返回原始 PDF 文件流，用于引用跳转与预览。"""
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if document.doc_type != "pdf":
        raise HTTPException(status_code=400, detail="Only PDF documents can be downloaded")
    file_path = Path(document.source_uri)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Source PDF file not found")
    return FileResponse(file_path, media_type="application/pdf")


@router.delete("/{doc_id}", status_code=204)
async def delete_document(doc_id: str, background_tasks: BackgroundTasks) -> None:
    """删除文档及其关联资源（级联清理）。

    清理顺序：
    1) 删除 Qdrant 中该 doc 的 chunk 向量；
    2) 删除 SQLite 文档记录（chunks 外键级联删除）；
    3) 删除本地上传 PDF 与解析输出目录。
    """
    await _delete_document_with_cascade(doc_id, background_tasks=background_tasks)


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_documents(
    body: BatchDeleteRequest, background_tasks: BackgroundTasks
) -> BatchDeleteResponse:
    """批量删除文档，允许部分失败并返回逐项结果。"""
    failed: list[dict[str, str]] = []
    deleted = 0
    for doc_id in body.doc_ids:
        try:
            await _delete_document_with_cascade(doc_id, background_tasks=background_tasks)
            deleted += 1
        except HTTPException as exc:
            failed.append(
                {
                    "doc_id": doc_id,
                    "code": _error_code_from_status(exc.status_code),
                    "message": str(exc.detail),
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "doc_id": doc_id,
                    "code": "DOCUMENT_DELETE_FAILED",
                    "message": f"{exc.__class__.__name__}: {exc}",
                }
            )
    return BatchDeleteResponse(
        requested=len(body.doc_ids),
        deleted=deleted,
        failed=failed,
    )


@router.post("/{doc_id}/reprocess", status_code=202)
async def reprocess_document(doc_id: str, background_tasks: BackgroundTasks) -> dict:
    """对已有文档重新触发 ingestion。

    - processing 状态下禁止重复触发，避免并发重复处理；
    - 将状态回写为 pending，复用同一条流水线逻辑。
    """
    sqlite_store = get_sqlite_store()
    document = await sqlite_store.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    if document.status == "processing":
        raise HTTPException(status_code=409, detail="Document is currently processing")
    await sqlite_store.set_document_status(doc_id, "pending", error_message=None)
    background_tasks.add_task(_enqueue_ingest, doc_id)
    return {"doc_id": doc_id, "status": "pending", "message": "Reprocess queued"}
