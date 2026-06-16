"""对话路由（SSE 流式输出）。

本模块负责：
1) 创建会话；
2) 接收用户问题并触发 QA 流水线；
3) 以 SSE 事件流逐步返回模型答案与引用；
4) 持久化对话历史，支持回放。
"""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter
from fastapi import HTTPException
from sse_starlette.sse import EventSourceResponse

from api.dependencies import get_qa_pipeline, get_sqlite_store
from api.schemas import ChatMessageRequest, CreateSessionResponse
from stream_kg.config import settings
from stream_kg.kg.models import Citation
from stream_kg.kg.models import RetrievalMode
from stream_kg.kg.models import RetrievalChunk
from stream_kg.retrieval.meta_query import (
    build_locked_chunks,
    build_meta_answer,
    detect_meta_intent,
    is_any_browse_intent,
    is_browse_resume_intent,
    resolve_browse_target,
    resolve_browse_target_with_samples,
)
from stream_kg.retrieval.query_router import route_query
from stream_kg.retrieval.relevance_gate import assess_retrieval, should_use_evidence_fallback

router = APIRouter()


def _citation_payload(citation: Citation) -> dict[str, object]:
    return {
        "citation_id": citation.citation_id,
        "doc_id": citation.doc_id,
        "chunk_id": citation.chunk_id,
        "doc_title": citation.doc_title,
        "snippet": citation.snippet,
        "page_num": citation.page_num,
        "section_title": citation.section_title,
        "entities": citation.entities,
    }


@router.post("/sessions", status_code=201, response_model=CreateSessionResponse)
async def create_session() -> CreateSessionResponse:
    """创建一个新会话并写入 SQLite。"""
    session_id = str(uuid4())
    sqlite_store = get_sqlite_store()
    await sqlite_store.create_chat_session(session_id)
    return CreateSessionResponse(session_id=session_id)


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: str, body: ChatMessageRequest) -> EventSourceResponse:
    """发送用户消息并以 SSE 流式返回助手回答。

    事件顺序：
    - retrieval：检索统计信息；
    - token：LLM 实时 token；
    - citation：引用条目；
    - done：最终完成事件。
    """
    sqlite_store = get_sqlite_store()
    if not await sqlite_store.has_chat_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # 先持久化用户消息，保证会话审计完整。
    user_message_id = str(uuid4())
    await sqlite_store.append_chat_message(
        message_id=user_message_id,
        session_id=session_id,
        role="user",
        content=body.content,
        citations_json=None,
        retrieval_mode=body.retrieval_mode,
    )

    qa_pipeline = get_qa_pipeline()

    async def event_generator():
        # 为了获得真正流式体验，这里拆分为“检索 -> LLM 流式生成 -> 引用映射”三阶段。
        mode: RetrievalMode = body.retrieval_mode or route_query(body.content)
        if mode != "vector" and not settings.feature_graph_router_enabled:
            mode = "vector"

        assistant_text: str = ""
        ranked_chunks: list[RetrievalChunk] = []
        retrieved_chunks: list[RetrievalChunk] = []
        retrieval_source: str = "vector"
        locked_doc_id: str | None = None
        locked_doc_title: str | None = None
        meta_handled = False

        # ① 元问题（库里有几篇 / 都有什么）：跳过向量检索，直接基于 SQLite 元数据回答。
        meta_intent = detect_meta_intent(body.content)
        documents, _doc_total = (
            await sqlite_store.list_documents(limit=100, offset=0) if meta_intent or is_any_browse_intent(body.content) else ([], 0)
        )
        if meta_intent:
            meta_answer = build_meta_answer(meta_intent, documents=documents)
            retrieval_source = f"meta:{meta_intent}"
            meta_handled = True
            yield {
                "event": "retrieval",
                "data": json.dumps(
                    {
                        "mode": mode,
                        "chunk_count": 0,
                        "raw_chunk_count": 0,
                        "entity_count": 0,
                        "top_score": None,
                        "lexical_overlap": None,
                        "retrieval_reason": "meta_direct",
                        "retrieval_source": retrieval_source,
                        "reranker_mode": "off",
                    },
                    ensure_ascii=False,
                ),
            }
            yield {"event": "token", "data": json.dumps({"content": meta_answer.text}, ensure_ascii=False)}
            assistant_text = meta_answer.text

        if not meta_handled:
            # ② 浏览意图（看简历 / 看刚导入文档 / 看最新链接）：锁定唯一目标文档，禁止混入其它 doc 的 chunk。
            browse_target = resolve_browse_target(body.content, documents) if documents else None
            if not browse_target and is_any_browse_intent(body.content):
                docs_all, _ = await sqlite_store.list_documents(limit=100, offset=0)
                browse_target = resolve_browse_target(body.content, docs_all)
                documents = docs_all

            # 简历意图但标题不含「简历/resume/cv」时，按首段 chunk 内容启发式兜底。
            if (
                not browse_target
                and is_browse_resume_intent(body.content)
                and documents
            ):
                head_text_by_doc: dict[str, str] = {}
                for doc in documents:
                    if doc.status != "ready":
                        continue
                    try:
                        head_chunks = await sqlite_store.list_chunks_by_doc(doc.doc_id)
                    except Exception:
                        head_chunks = []
                    sample_parts: list[str] = []
                    for chunk in head_chunks[:3]:
                        sample_parts.append(chunk.content or "")
                        if sum(len(p) for p in sample_parts) >= 1500:
                            break
                    head_text_by_doc[doc.doc_id] = "\n".join(sample_parts)
                browse_target = resolve_browse_target_with_samples(
                    body.content,
                    documents,
                    head_text_by_doc=head_text_by_doc,
                )

            reranker_mode: str = "off"
            if browse_target:
                doc_chunks = await sqlite_store.list_chunks_by_doc(browse_target.doc_id)
                ranked_chunks = build_locked_chunks(
                    doc_chunks,
                    is_usable=qa_pipeline.rag_generator.is_context_text_usable,
                )
                retrieval_source = f"browse:{browse_target.reason}"
                locked_doc_id = browse_target.doc_id
                locked_doc_title = browse_target.title
            else:
                top_k = settings.retrieval_top_k
                retrieved_chunks = await qa_pipeline.vector_search.search(query=body.content, top_k=top_k)
                ranked_chunks = [
                    c for c in retrieved_chunks if qa_pipeline.rag_generator.is_context_text_usable(c.chunk.content)
                ]
                retrieval_source = "vector"
                # 浏览模式不重排（已锁定文档，顺序由 chunk 顺序决定）；
                # 通用 vector 检索可以二阶段 rerank，提升 top-1 命中率。
                if (
                    qa_pipeline.reranker is not None
                    and qa_pipeline.reranker.enabled
                    and ranked_chunks
                ):
                    ranked_chunks = await qa_pipeline.reranker.rerank(
                        query=body.content,
                        chunks=ranked_chunks,
                        top_k=settings.rerank_top_k,
                    )
                    reranker_mode = qa_pipeline.reranker.last_backend_mode

            # 浏览模式下跳过相关性门控（强信任锁定文档）；普通向量检索仍要门控。
            if retrieval_source.startswith("browse:"):
                retrieval_acceptable = bool(ranked_chunks)
                is_low_confidence = False
                top_score = ranked_chunks[0].score if ranked_chunks else None
                retrieval_assessment = {
                    "acceptable": retrieval_acceptable,
                    "low_confidence": False,
                    "reason": "browse_locked",
                    "top_score": top_score,
                    "lexical_overlap": None,
                }
            else:
                retrieval_assessment = assess_retrieval(
                    body.content,
                    ranked_chunks,
                    min_score=settings.rag_min_relevance_score,
                    low_margin=settings.rag_low_relevance_margin,
                )
                top_score = retrieval_assessment["top_score"]
                is_low_confidence = bool(retrieval_assessment["low_confidence"])
                retrieval_acceptable = bool(retrieval_assessment["acceptable"])

            yield {
                "event": "retrieval",
                "data": json.dumps(
                    {
                        "mode": mode,
                        "chunk_count": len(ranked_chunks),
                        "raw_chunk_count": len(retrieved_chunks),
                        "entity_count": 0,
                        "top_score": top_score,
                        "lexical_overlap": retrieval_assessment.get("lexical_overlap"),
                        "retrieval_reason": retrieval_assessment.get("reason"),
                        "retrieval_source": retrieval_source,
                        "locked_doc_id": locked_doc_id,
                        "locked_doc_title": locked_doc_title,
                        "reranker_mode": reranker_mode,
                    },
                    ensure_ascii=False,
                ),
            }

            if not ranked_chunks and retrieved_chunks:
                assistant_text = (
                    "已命中资料，但文本提取质量较差（可能是 PDF 编码/反爬页面导致乱码），"
                    "当前无法给出可靠回答。建议重处理文档后再试。"
                )
                yield {"event": "token", "data": json.dumps({"content": assistant_text}, ensure_ascii=False)}
            elif not ranked_chunks and retrieval_source.startswith("browse:"):
                assistant_text = (
                    f"已定位到目标文档「{locked_doc_title or locked_doc_id}」，"
                    "但其文本内容当前不可用（可能解析失败或全部被质量过滤），建议在文档区重新处理。"
                )
                yield {"event": "token", "data": json.dumps({"content": assistant_text}, ensure_ascii=False)}
            elif not ranked_chunks:
                assistant_text = "根据已有资料无法回答该问题，请先导入相关文档后再试。"
                yield {"event": "token", "data": json.dumps({"content": assistant_text}, ensure_ascii=False)}
            elif not retrieval_acceptable:
                reason = str(retrieval_assessment["reason"])
                if reason == "no_lexical_overlap":
                    assistant_text = (
                        "资料库中没有与问题直接相关的内容。"
                        "请补充与问题主题相关的文档后再问，"
                        "或改用已导入资料里出现的表述（如人名、术语、章节要点）。"
                    )
                else:
                    assistant_text = (
                        "当前检索到的资料与问题相关性较低，暂时无法给出可靠回答。"
                        "建议补充更相关文档，或把问题描述得更具体。"
                    )
                yield {"event": "token", "data": json.dumps({"content": assistant_text}, ensure_ascii=False)}
            else:
                # 浏览模式下追加显式指令，避免模型拒答整篇可读文档。
                browse_hint = ""
                if retrieval_source.startswith("browse:"):
                    browse_hint = (
                        f"\n\n补充指令：上下文来自已锁定文档「{locked_doc_title or locked_doc_id}」，"
                        "请直接基于上下文给出条理清晰的总结，避免出现“无法回答”。"
                    )
                prompt = await qa_pipeline.rag_generator.build_prompt(query=body.content, chunks=ranked_chunks)
                if is_low_confidence:
                    prompt += "\n\n补充要求：若证据不充分，请先说明不确定点，再给出可参考的有限结论。"
                if browse_hint:
                    prompt += browse_hint

                assistant_parts: list[str] = []
                streamed_parts: list[str] = []
                guard_buffer = ""
                guard_flushed = False
                used_refusal_fallback = False
                # 浏览模式禁用证据兜底（已经基于锁定文档），避免「证据摘录式总结」模板出现。
                allow_evidence_fallback = not retrieval_source.startswith("browse:")

                async for token in qa_pipeline.rag_generator.stream_llm(prompt=prompt):
                    assistant_parts.append(token)
                    if used_refusal_fallback:
                        continue

                    if not guard_flushed:
                        guard_buffer += token
                        if qa_pipeline.rag_generator.is_refusal_answer(guard_buffer):
                            if allow_evidence_fallback and should_use_evidence_fallback(
                                body.content,
                                ranked_chunks,
                                fallback_min_score=settings.rag_evidence_fallback_min_score,
                            ):
                                assistant_text = qa_pipeline.rag_generator.build_evidence_fallback_answer(
                                    query=body.content, chunks=ranked_chunks
                                )
                                streamed_parts.append(assistant_text)
                                yield {
                                    "event": "token",
                                    "data": json.dumps({"content": assistant_text}, ensure_ascii=False),
                                }
                                used_refusal_fallback = True
                            else:
                                if retrieval_source.startswith("browse:"):
                                    assistant_text = (
                                        f"已锁定文档「{locked_doc_title or locked_doc_id}」，"
                                        "但模型未能给出总结。可尝试更具体的提问，例如"
                                        "「这份文档的目标岗位是什么」或「列出主要项目经验」。"
                                    )
                                else:
                                    assistant_text = (
                                        "根据已有资料无法可靠回答该问题。"
                                        "若这是通用概念题，请先导入相关主题文档；"
                                        "若问的是已导入内容，请把问题写得更具体。"
                                    )
                                streamed_parts.append(assistant_text)
                                yield {
                                    "event": "token",
                                    "data": json.dumps({"content": assistant_text}, ensure_ascii=False),
                                }
                                used_refusal_fallback = True
                            break

                        if len(guard_buffer) < 180:
                            continue

                        guard_flushed = True
                        streamed_parts.append(guard_buffer)
                        yield {"event": "token", "data": json.dumps({"content": guard_buffer}, ensure_ascii=False)}
                        guard_buffer = ""
                        continue

                    streamed_parts.append(token)
                    yield {"event": "token", "data": json.dumps({"content": token}, ensure_ascii=False)}

                if not used_refusal_fallback and not guard_flushed and guard_buffer:
                    streamed_parts.append(guard_buffer)
                    yield {"event": "token", "data": json.dumps({"content": guard_buffer}, ensure_ascii=False)}

                if streamed_parts:
                    assistant_text = "".join(streamed_parts).strip()
                else:
                    assistant_text = "".join(assistant_parts).strip()
                if not assistant_text:
                    assistant_text = "根据已有资料无法回答该问题，请稍后重试。"

        assistant_text, used_indexes = qa_pipeline.rag_generator.finalize_answer_with_citations(
            answer=assistant_text,
            chunk_count=len(ranked_chunks),
        )

        # 将引用索引映射为可展示 citation 信息。
        citations: list[Citation] = []
        citation_chunks: list[RetrievalChunk] = []
        for citation_index, source_index in enumerate(used_indexes, start=1):
            if source_index - 1 >= len(ranked_chunks):
                continue
            retrieval_chunk = ranked_chunks[source_index - 1]
            citation_chunks.append(retrieval_chunk)

        chunk_entities_map = await sqlite_store.list_chunk_entities_by_ids(
            [item.chunk.chunk_id for item in citation_chunks]
        )
        for citation_index, retrieval_chunk in enumerate(citation_chunks, start=1):
            chunk = retrieval_chunk.chunk
            document = await sqlite_store.get_document(chunk.doc_id)
            citations.append(
                Citation(
                    citation_id=str(citation_index),
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.chunk_id,
                    doc_title=document.title if document else chunk.doc_id,
                    snippet=chunk.content[:200],
                    page_num=chunk.page_num,
                    section_title=chunk.section_title,
                    entities=chunk_entities_map.get(chunk.chunk_id, []),
                )
            )

        for citation in citations:
            yield {
                "event": "citation",
                "data": json.dumps(_citation_payload(citation), ensure_ascii=False),
            }

        # 流式输出完成后持久化 assistant 消息与 citations。
        assistant_message_id = str(uuid4())
        await sqlite_store.append_chat_message(
            message_id=assistant_message_id,
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            citations_json=json.dumps(
                [_citation_payload(citation) for citation in citations],
                ensure_ascii=False,
            ),
            retrieval_mode=mode,
        )

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "message_id": assistant_message_id,
                    "citations": [
                        {
                            "citation_id": citation.citation_id,
                            "doc_title": citation.doc_title,
                            "page_num": citation.page_num,
                        }
                        for citation in citations
                    ],
                    "retrieval_mode": mode,
                },
                ensure_ascii=False,
            ),
        }

    return EventSourceResponse(event_generator())


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str) -> dict:
    """获取指定会话的完整消息历史。"""
    sqlite_store = get_sqlite_store()
    if not await sqlite_store.has_chat_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    messages = await sqlite_store.list_chat_messages(session_id)
    return {"messages": messages}
