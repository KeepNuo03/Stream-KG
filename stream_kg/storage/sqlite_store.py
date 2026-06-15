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

from stream_kg.kg.models import ChunkRecord, DocumentRecord, EntityMention, KgStatus, TemporalEdge


def _iso_now() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(UTC).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    """将 ISO 字符串解析为 datetime，空值返回 None。"""
    if not value:
        return None
    return datetime.fromisoformat(value)


def _row_to_document(row: aiosqlite.Row) -> DocumentRecord:
    """aiosqlite.Row → DocumentRecord（统一 list / get 路径）。

    兼容 P3-X 之前的旧库：
    - kg_status / kg_error_message 字段在 Phase A.5 才加，旧库可能不存在；
      用 try/get 兼容（aiosqlite.Row 不支持 .get，需用 KeyError catch）。
    """
    try:
        kg_status = row["kg_status"] or "unprocessed"
    except (IndexError, KeyError):
        kg_status = "unprocessed"
    try:
        kg_error_message = row["kg_error_message"]
    except (IndexError, KeyError):
        kg_error_message = None
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
        kg_status=kg_status,
        kg_error_message=kg_error_message,
    )


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
                    salience        REAL NOT NULL DEFAULT 0.5,
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
                    evidence            TEXT,
                    evidence_chunks_json TEXT NOT NULL DEFAULT '[]',
                    llm_confidence      REAL,
                    confidence          REAL NOT NULL,
                    valid_from          TEXT,
                    created_at          TEXT NOT NULL,
                    UNIQUE(head_entity_id, tail_entity_id, relation_type)
                );

                -- P3-X · Phase A：LLM 抽取日志（13 文档 §3.1，14 文档 A.5）。
                -- 每个 chunk 一行；attempt_count 累计含重试；status: 'ok'|'failed'
                CREATE TABLE IF NOT EXISTS kg_extraction_logs (
                    log_id              TEXT PRIMARY KEY,
                    chunk_id            TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    doc_id              TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                    status              TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
                    attempt_count       INTEGER NOT NULL DEFAULT 1,
                    elapsed_sec         REAL NOT NULL DEFAULT 0.0,
                    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
                    completion_tokens   INTEGER NOT NULL DEFAULT 0,
                    cost_yuan           REAL NOT NULL DEFAULT 0.0,
                    entities_count      INTEGER NOT NULL DEFAULT 0,
                    relations_count     INTEGER NOT NULL DEFAULT 0,
                    error_message       TEXT,
                    raw_output          TEXT,
                    created_at          TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_kg_logs_doc ON kg_extraction_logs(doc_id);
                CREATE INDEX IF NOT EXISTS idx_kg_logs_status ON kg_extraction_logs(status, created_at);

                -- P3-X · Phase B：文档-实体关联表（L0-L1）
                CREATE TABLE IF NOT EXISTS doc_entity_links (
                    doc_id           TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                    entity_id        TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
                    mention_count    INTEGER NOT NULL DEFAULT 0,
                    first_chunk_id   TEXT,
                    salience_max     REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (doc_id, entity_id)
                );
                CREATE INDEX IF NOT EXISTS idx_doc_entity_links_doc ON doc_entity_links(doc_id);
                CREATE INDEX IF NOT EXISTS idx_doc_entity_links_entity ON doc_entity_links(entity_id);
                """
            )

            # P3-X · Phase A：给已存在的 documents 表 ALTER 增字段（幂等）。
            # SQLite 的 ALTER TABLE ADD COLUMN 不支持 IF NOT EXISTS，
            # 必须先 PRAGMA table_info 检查，否则二次启动会抛 'duplicate column'。
            async with db.execute("PRAGMA table_info(documents)") as cur:
                cols = {row[1] for row in await cur.fetchall()}
            if "kg_status" not in cols:
                # SQLite ALTER ADD 不接受 CHECK / NOT NULL（无默认时）；
                # 用 DEFAULT 让旧行自动回填 'unprocessed'，约束由应用层 enforce。
                await db.execute(
                    "ALTER TABLE documents ADD COLUMN kg_status TEXT "
                    "NOT NULL DEFAULT 'unprocessed'"
                )
            if "kg_error_message" not in cols:
                await db.execute(
                    "ALTER TABLE documents ADD COLUMN kg_error_message TEXT"
                )

            # Phase B：entities 补 salience（兼容旧库）
            async with db.execute("PRAGMA table_info(entities)") as cur:
                entity_cols = {row[1] for row in await cur.fetchall()}
            if "salience" not in entity_cols:
                await db.execute(
                    "ALTER TABLE entities ADD COLUMN salience REAL NOT NULL DEFAULT 0.5"
                )

            # Phase B：temporal_edges 补证据字段（兼容旧库）
            async with db.execute("PRAGMA table_info(temporal_edges)") as cur:
                edge_cols = {row[1] for row in await cur.fetchall()}
            if "evidence" not in edge_cols:
                await db.execute("ALTER TABLE temporal_edges ADD COLUMN evidence TEXT")
            if "evidence_chunks_json" not in edge_cols:
                await db.execute(
                    "ALTER TABLE temporal_edges ADD COLUMN evidence_chunks_json TEXT "
                    "NOT NULL DEFAULT '[]'"
                )
            if "llm_confidence" not in edge_cols:
                await db.execute("ALTER TABLE temporal_edges ADD COLUMN llm_confidence REAL")

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
        return _row_to_document(row)

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

        documents = [_row_to_document(row) for row in rows]
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
        salience: float | None = None,
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
                        salience = MAX(COALESCE(?, salience), salience),
                        embedding_id = COALESCE(?, embedding_id),
                        last_updated_at = ?
                    WHERE entity_id = ?
                    """,
                    (
                        canonical_name,
                        entity_type,
                        json.dumps(merged_aliases, ensure_ascii=False),
                        description,
                        salience,
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
                        description, salience, embedding_id, first_seen_at, last_updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_id,
                        canonical_name,
                        entity_type,
                        json.dumps(aliases or [], ensure_ascii=False),
                        description,
                        salience if salience is not None else 0.5,
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
        evidence_chunks_json = (
            json.dumps([edge.evidence_chunk_id], ensure_ascii=False)
            if edge.evidence_chunk_id
            else "[]"
        )
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO temporal_edges(
                    edge_id, head_entity_id, tail_entity_id, relation_type,
                    evidence_chunk_id, evidence, evidence_chunks_json, llm_confidence,
                    confidence, valid_from, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(head_entity_id, tail_entity_id, relation_type)
                DO UPDATE SET
                    edge_id = excluded.edge_id,
                    evidence_chunk_id = excluded.evidence_chunk_id,
                    evidence = COALESCE(excluded.evidence, temporal_edges.evidence),
                    evidence_chunks_json = excluded.evidence_chunks_json,
                    llm_confidence = COALESCE(excluded.llm_confidence, temporal_edges.llm_confidence),
                    confidence = excluded.confidence,
                    created_at = excluded.created_at
                """,
                (
                    edge.edge_id,
                    edge.head_entity_id,
                    edge.tail_entity_id,
                    edge.relation_type,
                    edge.evidence_chunk_id,
                    None,
                    evidence_chunks_json,
                    edge.confidence,
                    edge.confidence,
                    None,
                    edge.created_at.isoformat(),
                ),
            )
            await db.commit()

    async def upsert_doc_entity_link(
        self,
        *,
        doc_id: str,
        entity_id: str,
        mention_count_delta: int = 1,
        first_chunk_id: str | None = None,
        salience: float = 0.0,
    ) -> None:
        """Upsert 文档-实体关联（L0-L1 containment 元数据）。"""
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO doc_entity_links(
                    doc_id, entity_id, mention_count, first_chunk_id, salience_max
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(doc_id, entity_id)
                DO UPDATE SET
                    mention_count = doc_entity_links.mention_count + excluded.mention_count,
                    first_chunk_id = COALESCE(doc_entity_links.first_chunk_id, excluded.first_chunk_id),
                    salience_max = MAX(doc_entity_links.salience_max, excluded.salience_max)
                """,
                (
                    doc_id,
                    entity_id,
                    max(mention_count_delta, 0),
                    first_chunk_id,
                    salience,
                ),
            )
            await db.commit()

    async def list_doc_entity_links(self, *, doc_id: str | None = None) -> list[dict[str, Any]]:
        """返回 doc_entity_links（可按 doc_id 过滤）。"""
        where = "WHERE doc_id = ?" if doc_id else ""
        args: tuple[Any, ...] = (doc_id,) if doc_id else ()
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                f"""
                SELECT doc_id, entity_id, mention_count, first_chunk_id, salience_max
                FROM doc_entity_links
                {where}
                ORDER BY salience_max DESC, mention_count DESC
                """,
                args,
            )
        return [dict(row) for row in rows]

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

    # ==========================================================================
    # P3-X · Phase A：LLM KG 抽取状态 / 日志
    # ==========================================================================

    async def set_document_kg_status(
        self,
        doc_id: str,
        kg_status: KgStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """更新文档的 KG 抽取状态。

        - 'extracting' / 'ready' 时建议传 error_message=None 清空旧错误；
        - 'failed' 时务必传 error_message 便于前端展示原因。
        """
        async with self._connection() as db:
            await db.execute(
                """
                UPDATE documents
                SET kg_status = ?,
                    kg_error_message = ?
                WHERE doc_id = ?
                """,
                (kg_status, error_message, doc_id),
            )
            await db.commit()

    async def insert_kg_extraction_log(
        self,
        *,
        log_id: str,
        chunk_id: str,
        doc_id: str,
        status: str,
        attempt_count: int,
        elapsed_sec: float,
        prompt_tokens: int,
        completion_tokens: int,
        cost_yuan: float,
        entities_count: int,
        relations_count: int,
        error_message: str | None = None,
        raw_output: str | None = None,
    ) -> None:
        """写入一条 chunk 级抽取日志（成本 + 监控 + debug 三用）。

        status 必须是 'ok' 或 'failed'（SQLite CHECK 约束）。
        raw_output 在 failed 时建议保留原始 LLM 响应，便于人工调 prompt。
        """
        async with self._connection() as db:
            await db.execute(
                """
                INSERT INTO kg_extraction_logs(
                    log_id, chunk_id, doc_id, status, attempt_count,
                    elapsed_sec, prompt_tokens, completion_tokens, cost_yuan,
                    entities_count, relations_count, error_message, raw_output,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    chunk_id,
                    doc_id,
                    status,
                    attempt_count,
                    elapsed_sec,
                    prompt_tokens,
                    completion_tokens,
                    cost_yuan,
                    entities_count,
                    relations_count,
                    error_message,
                    raw_output,
                    _iso_now(),
                ),
            )
            await db.commit()

    async def get_kg_extraction_stats(
        self, *, doc_id: str | None = None
    ) -> dict[str, Any]:
        """汇总 KG 抽取统计（监控 / 预算告警用）。

        - 不传 doc_id：全库汇总；
        - 传 doc_id：单文档汇总。
        """
        where = "WHERE doc_id = ?" if doc_id else ""
        args: tuple = (doc_id,) if doc_id else ()
        async with self._connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END), 0) AS ok_count,
                    COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END), 0) AS failed_count,
                    COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
                    COALESCE(SUM(cost_yuan), 0.0) AS total_cost_yuan,
                    COALESCE(SUM(entities_count), 0) AS total_entities,
                    COALESCE(SUM(relations_count), 0) AS total_relations
                FROM kg_extraction_logs
                {where}
                """,
                args,
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return {
                "total": 0,
                "ok_count": 0,
                "failed_count": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_cost_yuan": 0.0,
                "total_entities": 0,
                "total_relations": 0,
            }
        return dict(row)
