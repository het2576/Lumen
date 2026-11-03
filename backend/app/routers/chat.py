"""Chat endpoints.

POST /chat/stream  Server-Sent Events: the answer arrives token by token (used by the app).
POST /chat         The same pipeline returned as one JSON response (API clients, the evals).

Both run one pipeline, _answer(), which yields events:
    {"type": "meta",  "conversation_id": ...}            (stream only, sent first)
    {"type": "delta", "text": ...}                        (zero or more pieces of the answer)
    {"type": "done",  "answer": ..., "sources": [...], …} (the final ChatResponseOut)
    {"type": "error", "detail": ...}                      (stream only, if generation fails)
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app import db, tracing
from app.auth import get_current_user_id
from app.config import FULL_CONTEXT_MAX_CHUNKS, TOP_K
from app.models.schemas import ChatRequest, ChatResponseOut, ConversationHistoryOut, ConversationMessageOut, SourceOut
from app.services.generation import finalize_answer, stream_answer
from app.services.retrieval import embed_query, hybrid_search, semantic_search
from app.services.spreadsheet import execute_structured_query, is_tabular_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_STREAM_ERROR = "Lumen couldn't finish this answer. Please try again."


@router.get("/{conversation_id}/messages", response_model=ConversationHistoryOut)
def get_messages(conversation_id: str, user_id: str = Depends(get_current_user_id)):
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    document_ids = db.get_conversation_document_ids(conversation_id)
    if not document_ids or any(not db.get_document(document_id, user_id) for document_id in document_ids):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationHistoryOut(
        conversation_id=conversation_id,
        document_id=conversation["document_id"],
        document_ids=document_ids,
        messages=[ConversationMessageOut(**message) for message in db.get_all_conversation_messages(conversation_id)],
    )


def _documents_for_request(request: ChatRequest, user_id: str) -> list[dict]:
    document_ids = list(dict.fromkeys(request.document_ids or [request.document_id]))
    if request.document_id not in document_ids:
        document_ids.insert(0, request.document_id)
    documents = [db.get_document(document_id, user_id) for document_id in document_ids]
    if any(document is None for document in documents):
        raise HTTPException(status_code=404, detail="One or more documents were not found.")
    if any(document["status"] != "ready" for document in documents if document):
        raise HTTPException(status_code=409, detail="One or more documents are still being prepared.")
    return [document for document in documents if document]


def _gather_context(documents: list[dict], question: str, trace: tracing.Observation = tracing.NOOP) -> list[dict]:
    """Send small sources to the model whole so broad questions ("summarize", "key risks") see
    everything; fall back to hybrid retrieval of the closest chunks for large sources."""
    try:
        with trace.span("embed_query"):
            query_embedding = embed_query(question)
    except Exception as exc:
        logger.warning("Query embedding unavailable (%s); using keyword search only", exc)
        query_embedding = None

    chunk_counts = [document.get("chunk_count") for document in documents]
    whole_documents = (
        query_embedding is not None
        and None not in chunk_counts
        and sum(chunk_counts) <= FULL_CONTEXT_MAX_CHUNKS
    )
    trace.update(mode="whole_document" if whole_documents else "hybrid")

    def search(document: dict) -> list[dict]:
        with trace.span("search", document_id=document["id"]):
            if whole_documents:
                # match_count covers every chunk, so this returns the full document with real scores.
                found = semantic_search(document["id"], query_embedding, top_k=max(document["chunk_count"], 1))
                found = [{**chunk, "similarity_score": chunk["semantic_score"]} for chunk in found]
            else:
                found = hybrid_search(document["id"], question, query_embedding=query_embedding)
        return [{**chunk, "document_id": document["id"], "document_name": document["filename"]} for chunk in found]

    if len(documents) == 1:
        groups = [search(documents[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(len(documents), 8)) as pool:
            groups = list(pool.map(search, documents))

    if whole_documents:
        return [chunk for group in groups for chunk in sorted(group, key=lambda chunk: chunk["chunk_index"])]
    chunks = [chunk for group in groups for chunk in group]
    chunks.sort(key=lambda chunk: chunk["similarity_score"], reverse=True)
    return chunks[:TOP_K]


@dataclass
class _Turn:
    question: str
    primary_document_id: str
    documents: list[dict]
    conversation_id: str
    history: list[dict]
    tables: list[dict]


def _prepare(req: ChatRequest, user_id: str, trace: tracing.Trace) -> _Turn:
    """Validate the request and load its state. Runs before any response bytes are sent, so a
    missing document or conversation is still a proper 404/409, even for the stream."""
    with trace.span("prepare"):
        documents = _documents_for_request(req, user_id)
        document_ids = [document["id"] for document in documents]
        if req.conversation_id:
            conversation = db.get_conversation(req.conversation_id)
            if not conversation or set(db.get_conversation_document_ids(req.conversation_id)) != set(document_ids):
                raise HTTPException(status_code=404, detail="Conversation not found for these active sources.")
            conversation_id = req.conversation_id
        else:
            conversation_id = db.create_conversation(document_ids)

        history = db.get_conversation_history(conversation_id)
        document_by_id = {document["id"]: document for document in documents}
        tables = [{**table, "document_name": document_by_id[table["document_id"]]["filename"]} for table in db.get_document_tables(document_ids)]
    trace.session_id = conversation_id
    trace.set(conversation_id=conversation_id, documents=len(documents), history_messages=len(history))
    return _Turn(req.question, req.document_id, documents, conversation_id, history, tables)


def _retry_once(action: str, fn, *args) -> None:
    for attempt in range(2):
        try:
            fn(*args)
            return
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
            logger.exception("Could not %s", action)


def _persist(turn: _Turn, answer: str, cited_chunk_ids: list[str], avg_similarity: float | None, trace: tracing.Trace) -> None:
    """Runs after the answer was delivered: a failed write is logged, never turns a finished
    answer into an error."""
    with trace.span("persist"):
        _retry_once("save the conversation turn", db.save_turn, turn.conversation_id, turn.question, answer, cited_chunk_ids)
        _retry_once("log the query", db.log_query, turn.primary_document_id, turn.question, avg_similarity)


def _answer(turn: _Turn, trace: tracing.Trace) -> Iterator[dict]:
    if turn.tables and is_tabular_question(turn.question):
        with trace.span("structured_query") as span:
            try:
                structured = execute_structured_query(turn.question, turn.tables, span)
            except Exception:
                # A malformed extracted table must not fail the whole answer; text retrieval still works.
                logger.exception("Structured query failed; answering from document text instead")
                structured = {"success": False}
        if structured.get("success"):
            trace.set(route="structured")
            source_tables = structured.get("tables", [structured["table"]])
            sources = [SourceOut(
                chunk_id=f"table:{table['id']}", text=f"Verified table: {table['table_name']}",
                page_number=table.get("page_number"), similarity_score=1.0, document_id=table["document_id"],
                document_name=table["document_name"], table_name=table["table_name"], verification="verified",
            ) for table in source_tables]
            response = ChatResponseOut(conversation_id=turn.conversation_id, answer=structured["answer"], sources=sources, verification="verified", chart=structured.get("chart"))
            yield {"type": "done", **response.model_dump()}
            _persist(turn, structured["answer"], [], 1.0, trace)
            return

    trace.set(route="text")
    with trace.span("retrieve") as span:
        chunks = _gather_context(turn.documents, turn.question, span)
    trace.set(context_chunks=len(chunks))

    pieces: list[str] = []
    for piece in stream_answer(turn.question, chunks, turn.history, trace):
        pieces.append(piece)
        yield {"type": "delta", "text": piece}
    result = finalize_answer("".join(pieces), chunks)

    chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    sources = [SourceOut(chunk_id=chunk_id, text=chunk_by_id[chunk_id]["text"], page_number=chunk_by_id[chunk_id].get("page_number"), similarity_score=chunk_by_id[chunk_id]["similarity_score"], document_id=chunk_by_id[chunk_id]["document_id"], document_name=chunk_by_id[chunk_id]["document_name"], verification="text") for chunk_id in result["cited_chunk_ids"] if chunk_id in chunk_by_id]
    # A chart drawn from prose is flagged for the reader to verify, unlike table-computed charts.
    verification = "inferred" if "```chart" in result["answer"] else "text"
    response = ChatResponseOut(conversation_id=turn.conversation_id, answer=result["answer"], sources=sources, verification=verification)
    yield {"type": "done", **response.model_dump()}

    average = sum(chunk["similarity_score"] for chunk in chunks) / len(chunks) if chunks else None
    _persist(turn, result["answer"], result["cited_chunk_ids"], average, trace)


def _start_trace(req: ChatRequest, user_id: str, stream: bool) -> tracing.Trace:
    return tracing.Trace("chat", user_id=user_id, session_id=req.conversation_id, input=req.question, metadata={"stream": stream})


@router.post("", response_model=ChatResponseOut)
def chat(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
    trace = _start_trace(req, user_id, stream=False)
    try:
        turn = _prepare(req, user_id, trace)
        final = None
        # Drain the whole pipeline: the turn is saved after the "done" event.
        for event in _answer(turn, trace):
            if event["type"] == "done":
                final = event
        if final is None:
            raise RuntimeError("The answer pipeline finished without a result.")
    except HTTPException as exc:
        trace.finish(error=exc.detail)
        raise
    except Exception as exc:
        logger.exception("Chat request failed: %s", exc)
        trace.finish(error=exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    trace.finish(output=final["answer"])
    return ChatResponseOut(**{key: value for key, value in final.items() if key != "type"})


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _stream_events(turn: _Turn, trace: tracing.Trace) -> Iterator[str]:
    final_answer = None
    error = None
    try:
        yield _sse({"type": "meta", "conversation_id": turn.conversation_id})
        for event in _answer(turn, trace):
            if event["type"] == "done":
                final_answer = event["answer"]
            yield _sse(event)
    except GeneratorExit:
        error = "client disconnected"
        raise
    except Exception as exc:
        logger.exception("Streaming chat failed: %s", exc)
        error = exc
        yield _sse({"type": "error", "detail": _STREAM_ERROR})
    finally:
        trace.finish(output=final_answer, error=error)


@router.post("/stream")
def chat_stream(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
    trace = _start_trace(req, user_id, stream=True)
    try:
        turn = _prepare(req, user_id, trace)
    except HTTPException as exc:
        trace.finish(error=exc.detail)
        raise
    except Exception as exc:
        logger.exception("Chat request failed: %s", exc)
        trace.finish(error=exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return StreamingResponse(
        _stream_events(turn, trace),
        media_type="text/event-stream",
        # no-transform/X-Accel-Buffering stop proxies from buffering the stream into one blob.
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
