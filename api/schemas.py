"""API request/response Pydantic models — align with docs/spec/06-api.md."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

DocType = Literal["pdf", "web"]
DocStatus = Literal["pending", "processing", "ready", "failed"]
RetrievalMode = Literal["vector", "graph", "hybrid"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class DocumentQueuedResponse(BaseModel):
    doc_id: str
    status: DocStatus
    message: str


class ImportUrlRequest(BaseModel):
    url: HttpUrl
    title: str | None = None


class BatchDeleteRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=200)


class BatchDeleteFailedItem(BaseModel):
    doc_id: str
    code: str
    message: str


class BatchDeleteResponse(BaseModel):
    requested: int
    deleted: int
    failed: list[BatchDeleteFailedItem] = Field(default_factory=list)


class DocumentSummary(BaseModel):
    doc_id: str
    title: str
    doc_type: DocType
    status: DocStatus
    page_count: int | None = None
    ingested_at: datetime | None = None
    entity_count: int = 0
    chunk_count: int = 0
    error_message: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]
    total: int


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatMessageRequest(BaseModel):
    content: str
    retrieval_mode: RetrievalMode | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict[str, str]
    gpu: dict[str, int | bool]
