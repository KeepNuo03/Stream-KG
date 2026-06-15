"""Domain models for Phase 1/2 pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

DocType = Literal["pdf", "web"]
DocStatus = Literal["pending", "processing", "ready", "failed"]
RetrievalMode = Literal["vector", "graph", "hybrid"]
EntityType = Literal["concept", "method", "person", "dataset", "metric", "organization"]
ResolveAction = Literal["merge", "create"]
TemporalRelType = Literal["improves", "contradicts", "extends", "surveys", "mentions"]


KgStatus = Literal["unprocessed", "extracting", "ready", "failed"]


@dataclass(slots=True)
class DocumentRecord:
    """Persistent document metadata."""

    doc_id: str
    title: str
    doc_type: DocType
    source_uri: str
    status: DocStatus
    ingested_at: datetime
    published_at: datetime | None = None
    page_count: int | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # P3-X · Phase A：知识抽取状态（独立于 ingest 的 status）。
    # 旧库 ALTER ADD COLUMN 后默认值 = 'unprocessed'（见 sqlite_store.initialize）。
    kg_status: KgStatus = "unprocessed"
    kg_error_message: str | None = None


@dataclass(slots=True)
class ChunkRecord:
    """A parsed text chunk from one source document."""

    chunk_id: str
    doc_id: str
    content: str
    chunk_type: str
    char_start: int
    char_end: int
    token_count: int
    page_num: int | None = None
    section_title: str | None = None
    embedding_id: str | None = None


@dataclass(slots=True)
class Citation:
    """Citation object returned to clients."""

    citation_id: str
    doc_id: str
    chunk_id: str
    doc_title: str
    snippet: str
    page_num: int | None = None
    section_title: str | None = None


@dataclass(slots=True)
class RetrievalChunk:
    """Chunk candidate returned from vector retrieval."""

    chunk: ChunkRecord
    score: float


@dataclass(slots=True)
class QaResult:
    """Final QA output before SSE serialization."""

    answer: str
    citations: list[Citation]
    retrieval_mode: RetrievalMode
    chunk_count: int
    entity_count: int = 0


@dataclass(slots=True)
class EntityMention:
    """One entity mention extracted from a chunk."""

    mention_id: str
    doc_id: str
    chunk_id: str
    surface_form: str
    entity_type: EntityType
    char_start: int
    char_end: int
    context_snippet: str
    vector: list[float] | None = None
    entity_id: str | None = None


@dataclass(slots=True)
class EntityCandidate:
    """Candidate entity returned by ANN retrieval."""

    entity_id: str
    canonical_name: str
    entity_type: EntityType
    score: float
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolveResult:
    """Online resolve decision for one mention."""

    action: ResolveAction
    entity_id: str
    canonical_name: str
    entity_type: EntityType
    score: float


@dataclass(slots=True)
class TemporalEdge:
    """Temporal/semantic relation between two entities."""

    edge_id: str
    head_entity_id: str
    tail_entity_id: str
    relation_type: TemporalRelType
    confidence: float
    evidence_chunk_id: str
    created_at: datetime


def new_mention_id() -> str:
    """Create a mention UUID string."""
    return str(uuid4())


def new_edge_id() -> str:
    """Create an edge UUID string."""
    return str(uuid4())
