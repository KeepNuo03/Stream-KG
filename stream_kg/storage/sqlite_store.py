"""SQLite 元数据存储层。

说明：
- 这里是 MVP 的“权威元数据源”，保存文档、chunk、会话消息；
- 通过外键 ON DELETE CASCADE 实现基础级联清理；
- 对外暴露的接口尽量薄，便于后续替换成 ORM 或其他数据库。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from stream_kg.kg.models import ChunkRecord, DocumentRecord, EntityMention, TemporalEdge


def _iso_now() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(UTC).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    """将 ISO 字符串解析为 datetime，空值返回 None。"""
    if not value:
        return None
    return datetime.fromisoformat(value)


class SQLiteStore:
    """基于 aiosqlite 的异步仓储实现。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """创建连接并启用外键级联。"""
        db = await aiosqlite.connect(self.db_path)
        await db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        """初始化数据库与表结构（幂等）。"""
        db_parent = Path(self.db_path).parent
        db_parent.mkdir(parents=True, exist_ok=True)

        async with self._connection() as db:
            # 统一在启动时建表，避免首次请求触发表结构竞争。
            await db.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS documents (
                    doc_id          TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    doc_type        TEXT NOT NULL CHECK (doc_type IN ('pdf', 'web')),
                    source_uri      TEXT NOT NULL,
                    status          TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'ready', 'failed')),
                    published_at    TEXT,
                    ingested_at     TEXT NOT NULL,
                    page_count      INTEGER,
                    error_message   TEXT,
                    metadata_json   TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id        TEXT PRIMARY KEY,
                    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                    content         TEXT NOT NULL,
                    chunk_type      TEXT NOT NULL,
                    page_num        INTEGER,
                    section_title   TEXT,
                    char_start      INTEGER NOT NULL,
                    char_end        INTEGER NOT NULL,
                    token_count     INTEGER NOT NULL,
                    embedding_id    TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id      TEXT PRIMARY KEY,
                    created_at      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id      TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content         TEXT NOT NULL,
                    citations_json  TEXT,
                    retrieval_mode  TEXT,
                    created_at      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);

                CREATE TABLE IF NOT EXISTS entities (
                    entity_id       TEXT PRIMARY KEY,
                    canonical_name  TEXT NOT NULL,
                    entity_type     TEXT NOT NULL,
                    aliases_json    TEXT NOT NULL DEFAULT '[]',
                    description     TEXT,
                    embedding_id    TEXT,
                    first_seen_at   TEXT NOT NULL,
                    last_updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS entity_mentions (
                    mention_id      TEXT PRIMARY KEY,
                    entity_id       TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    chunk_id        TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                    surface_form    TEXT NOT NULL,
                    char_start      INTEGER NOT NULL,
                    char_end        INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
                CREATE INDEX IF NOT EXISTS idx_mentions_doc ON entity_mentions(doc_id);

                CREATE TABLE IF NOT EXISTS temporal_edges (
                    edge_id             TEXT PRIMARY KEY,
                    head_entity_id      TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    tail_entity_id      TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    relation_type       TEXT NOT NULL,
                    evidence_chunk_id   TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    confidence          REAL NOT NULL,
                    valid_from          TEXT,
                    created_at          TEXT NOT NULL,
                    UNIQUE(head_entity_id, tail_entity_id, relation_type)
                );
                """
            )
            await db.commit()

    async def create_document(
        self,
        *,
        doc_id: str,
        title: str,
        doc_type: str,
        source_uri: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """创建文档记录，初始状态固定为 pending。"""
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO documents(doc_id, title, doc_type, source_uri, status, ingested_at, metadata_json)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (doc_id, title, doc_type, source_uri, _iso_now(), json.dumps(metadata or {})),
            )
            await db.commit()

    async def set_document_status(
        self,
        doc_id: str,
        status: str,
        *,
        page_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """更新文档状态，并可附带页数/错误信息。"""
        async with self._connection() as db:
            await db.execute(
                """
                UPDATE documents
                SET status = ?,
                    page_count = COALESCE(?, page_count),
                    error_message = ?
                WHERE doc_id = ?
                """,
                (status, page_count, error_message, doc_id),
            )
            await db.commit()

    async def update_document_title(self, doc_id: str, title: str) -> None:
        """更新文档标题。"""
        normalized = title.strip()
        if not normalized:
            return
        async with self._connection() as db:
            await db.execute(
                """
                UPDATE documents
                SET title = ?
                WHERE doc_id = ?
                """,
                (normalized, doc_id),
            )
            await db.commit()

    async def get_document(self, doc_id: str) -> DocumentRecord | None:
        """按 doc_id 获取文档，不存在返回 None。"""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return DocumentRecord(
            doc_id=row["doc_id"],
            title=row["title"],
            doc_type=row["doc_type"],
            source_uri=row["source_uri"],
            status=row["status"],
            ingested_at=_from_iso(row["ingested_at"]) or datetime.now(UTC),
            published_at=_from_iso(row["published_at"]),
            page_count=row["page_count"],
            error_message=row["error_message"],
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    async def list_documents(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentRecord], int]:
        """按条件分页列出文档并返回总数。"""
        where = ""
        args: list[Any] = []
        if status:
            where = "WHERE status = ?"
            args.append(status)

        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT COUNT(*) AS total FROM documents {where}",
                tuple(args),
            ) as cur:
                total_row = await cur.fetchone()
            # 先 count 再分页查询，保证前端分页总数可用。
            rows = await db.execute_fetchall(
                f"SELECT * FROM documents {where} ORDER BY ingested_at DESC LIMIT ? OFFSET ?",
                tuple(args + [limit, offset]),
            )

        documents = [
            DocumentRecord(
                doc_id=row["doc_id"],
                title=row["title"],
                doc_type=row["doc_type"],
                source_uri=row["source_uri"],
                status=row["status"],
                ingested_at=_from_iso(row["ingested_at"]) or datetime.now(UTC),
                published_at=_from_iso(row["published_at"]),
                page_count=row["page_count"],
                error_message=row["error_message"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row in rows
        ]
        total = int(total_row["total"]) if total_row else 0
        return documents, total

    async def delete_document(self, doc_id: str) -> None:
        """删除文档元数据（chunks 依赖外键级联删除）。"""
        async with self._connection() as db:
            await db.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            await db.commit()

    async def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        """批量写入 chunk 元数据。"""
        if not chunks:
            return
        async with self._connection() as db:
            await db.executemany(
                """
                INSERT OR REPLACE INTO chunks(
                    chunk_id, doc_id, content, chunk_type, page_num, section_title,
                    char_start, char_end, token_count, embedding_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.chunk_id,
                        c.doc_id,
                        c.content,
                        c.chunk_type,
                        c.page_num,
                        c.section_title,
                        c.char_start,
                        c.char_end,
                        c.token_count,
                        c.embedding_id,
                    )
                    for c in chunks
                ],
            )
            await db.commit()

    async def list_chunks_by_doc(self, doc_id: str) -> list[ChunkRecord]:
        """获取某文档的全部 chunk（按 char_start 排序）。"""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM chunks WHERE doc_id = ? ORDER BY char_start ASC",
                (doc_id,),
            )
        return [
            ChunkRecord(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                content=row["content"],
                chunk_type=row["chunk_type"],
                page_num=row["page_num"],
                section_title=row["section_title"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                token_count=row["token_count"],
                embedding_id=row["embedding_id"],
            )
            for row in rows
        ]

    async def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, ChunkRecord]:
        """按 chunk_id 列表批量查询，并返回 map 便于上游对齐结果顺序。"""
        if not chunk_ids:
            return {}
        placeholders = ",".join(["?"] * len(chunk_ids))
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
                tuple(chunk_ids),
            )
        return {
            row["chunk_id"]: ChunkRecord(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                content=row["content"],
                chunk_type=row["chunk_type"],
                page_num=row["page_num"],
                section_title=row["section_title"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                token_count=row["token_count"],
                embedding_id=row["embedding_id"],
            )
            for row in rows
        }

    async def create_chat_session(self, session_id: str) -> None:
        """创建会话（若已存在则覆盖更新时间）。"""
        async with self._connection() as db:
            await db.execute(
                "INSERT OR REPLACE INTO chat_sessions(session_id, created_at) VALUES (?, ?)",
                (session_id, _iso_now()),
            )
            await db.commit()

    async def has_chat_session(self, session_id: str) -> bool:
        """判断会话是否存在。"""
        async with self._connection() as db:
            async with db.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = ?",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
        return row is not None

    async def append_chat_message(
        self,
        *,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        citations_json: str | None = None,
        retrieval_mode: str | None = None,
    ) -> None:
        """追加一条会话消息。"""
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO chat_messages(
                    message_id, session_id, role, content, citations_json, retrieval_mode, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, citations_json, retrieval_mode, _iso_now()),
            )
            await db.commit()

    async def list_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        """按时间顺序返回会话历史。"""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT message_id, role, content, citations_json, retrieval_mode, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
        return [
            {
                "message_id": row["message_id"],
                "role": row["role"],
                "content": row["content"],
                "citations": json.loads(row["citations_json"]) if row["citations_json"] else [],
                "retrieval_mode": row["retrieval_mode"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def stats(self) -> dict[str, int]:
        """返回核心对象数量统计。"""
        async with self._connection() as db:
            async with db.execute("SELECT COUNT(*) FROM documents") as cur:
                documents = await cur.fetchone()
            async with db.execute("SELECT COUNT(*) FROM chunks") as cur:
                chunks = await cur.fetchone()
            async with db.execute("SELECT COUNT(*) FROM chat_sessions") as cur:
                sessions = await cur.fetchone()
            async with db.execute("SELECT COUNT(*) FROM chat_messages") as cur:
                messages = await cur.fetchone()
            async with db.execute("SELECT COUNT(*) FROM entities") as cur:
                entities = await cur.fetchone()
            async with db.execute("SELECT COUNT(*) FROM temporal_edges") as cur:
                edges = await cur.fetchone()
        return {
            "documents": int(documents[0]) if documents else 0,
            "chunks": int(chunks[0]) if chunks else 0,
            "sessions": int(sessions[0]) if sessions else 0,
            "messages": int(messages[0]) if messages else 0,
            "entities": int(entities[0]) if entities else 0,
            "edges": int(edges[0]) if edges else 0,
        }

    async def upsert_entity(
        self,
        *,
        entity_id: str,
        canonical_name: str,
        entity_type: str,
        aliases: list[str] | None = None,
        description: str | None = None,
        embedding_id: str | None = None,
    ) -> None:
        """写入或更新实体元数据。"""
        now = _iso_now()
        async with self._connection() as db:
            async with db.execute(
                "SELECT entity_id FROM entities WHERE entity_id = ?",
                (entity_id,),
            ) as cur:
                exists = await cur.fetchone()
            if exists:
                merged_aliases = aliases or []
                async with db.execute(
                    "SELECT aliases_json FROM entities WHERE entity_id = ?",
                    (entity_id,),
                ) as cur:
                    row = await cur.fetchone()
                if row and row[0]:
                    try:
                        existing_aliases = json.loads(row[0])
                        if isinstance(existing_aliases, list):
                            merged_aliases = sorted(
                                {str(item) for item in existing_aliases}
                                | {str(item) for item in (aliases or [])}
                            )
                    except json.JSONDecodeError:
                        pass
                await db.execute(
                    """
                    UPDATE entities
                    SET canonical_name = ?,
                        entity_type = ?,
                        aliases_json = ?,
                        description = COALESCE(?, description),
                        embedding_id = COALESCE(?, embedding_id),
                        last_updated_at = ?
                    WHERE entity_id = ?
                    """,
                    (
                        canonical_name,
                        entity_type,
                        json.dumps(merged_aliases, ensure_ascii=False),
                        description,
                        embedding_id,
                        now,
                        entity_id,
                    ),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO entities(
                        entity_id, canonical_name, entity_type, aliases_json,
                        description, embedding_id, first_seen_at, last_updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        canonical_name,
                        entity_type,
                        json.dumps(aliases or [], ensure_ascii=False),
                        description,
                        embedding_id,
                        now,
                        now,
                    ),
                )
            await db.commit()

    async def insert_entity_mention(self, mention: EntityMention) -> None:
        """写入一条 mention 记录。"""
        if not mention.entity_id:
            return
        async with self._connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO entity_mentions(
                    mention_id, entity_id, chunk_id, doc_id, surface_form, char_start, char_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mention.mention_id,
                    mention.entity_id,
                    mention.chunk_id,
                    mention.doc_id,
                    mention.surface_form,
                    mention.char_start,
                    mention.char_end,
                ),
            )
            await db.commit()

    async def upsert_temporal_edge(self, edge: TemporalEdge) -> None:
        """写入或更新时序关系边。"""
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO temporal_edges(
                    edge_id, head_entity_id, tail_entity_id, relation_type,
                    evidence_chunk_id, confidence, valid_from, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(head_entity_id, tail_entity_id, relation_type)
                DO UPDATE SET
                    edge_id = excluded.edge_id,
                    evidence_chunk_id = excluded.evidence_chunk_id,
                    confidence = excluded.confidence,
                    created_at = excluded.created_at
                """,
                (
                    edge.edge_id,
                    edge.head_entity_id,
                    edge.tail_entity_id,
                    edge.relation_type,
                    edge.evidence_chunk_id,
                    edge.confidence,
                    None,
                    edge.created_at.isoformat(),
                ),
            )
            await db.commit()

    async def list_entities_by_doc(self, doc_id: str) -> list[dict[str, Any]]:
        """列出与文档关联的实体。"""
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT DISTINCT e.*
                FROM entities e
                JOIN entity_mentions m ON m.entity_id = e.entity_id
                WHERE m.doc_id = ?
                """,
                (doc_id,),
            )
        return [dict(row) for row in rows]

    async def count_entities_by_doc(self, doc_id: str) -> int:
        """统计文档关联实体数量。"""
        async with self._connection() as db:
            async with db.execute(
                """
                SELECT COUNT(DISTINCT entity_id)
                FROM entity_mentions
                WHERE doc_id = ?
                """,
                (doc_id,),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_orphan_entity_ids(self) -> list[str]:
        """返回没有任何 mention 的实体 ID。"""
        async with self._connection() as db:
            rows = await db.execute_fetchall(
                """
                SELECT e.entity_id
                FROM entities e
                LEFT JOIN entity_mentions m ON m.entity_id = e.entity_id
                WHERE m.entity_id IS NULL
                """
            )
        return [str(row[0]) for row in rows]

    async def delete_entity(self, entity_id: str) -> None:
        """删除实体（mentions/edges 由外键级联）。"""
        async with self._connection() as db:
            await db.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))
            await db.commit()
