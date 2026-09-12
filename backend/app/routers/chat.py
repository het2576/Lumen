import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import get_current_user_id
from app.models.schemas import ChatRequest, ChatResponseOut, ConversationHistoryOut, ConversationMessageOut, SourceOut
from app.services.generation import generate_answer
from app.services.retrieval import embed_query, hybrid_search
from app.services.spreadsheet import execute_structured_query, is_tabular_question

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


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


@router.post("", response_model=ChatResponseOut)
def chat(req: ChatRequest, user_id: str = Depends(get_current_user_id)):
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

    if is_tabular_question(req.question):
        structured = execute_structured_query(req.question, tables)
        if structured.get("success"):
            source_tables = structured.get("tables", [structured["table"]])
            sources = [SourceOut(
                chunk_id=f"table:{table['id']}", text=f"Verified table: {table['table_name']}",
                page_number=table.get("page_number"), similarity_score=1.0, document_id=table["document_id"],
                document_name=table["document_name"], table_name=table["table_name"], verification="verified",
            ) for table in source_tables]
            db.log_query(req.document_id, req.question, 1.0)
            db.save_message(conversation_id, "user", req.question)
            db.save_message(conversation_id, "assistant", structured["answer"], [])
            return ChatResponseOut(conversation_id=conversation_id, answer=structured["answer"], sources=sources, verification="verified", chart=structured.get("chart"))
        if not tables and any(word in req.question.lower() for word in ("chart", "trend", "average", "total", "sum", "compare")):
            answer = "I found no verified structured table for that numeric request. I can discuss numbers mentioned in the text, but I won’t present an inferred calculation or chart as verified data."
            db.save_message(conversation_id, "user", req.question)
            db.save_message(conversation_id, "assistant", answer, [])
            return ChatResponseOut(conversation_id=conversation_id, answer=answer, sources=[], verification="inferred")

    try:
        try:
            query_embedding = embed_query(req.question)
        except Exception as exc:
            logger.warning("Query embedding unavailable (%s); using keyword search only", exc)
            query_embedding = None

        def search(document: dict) -> list[dict]:
            return [
                {**chunk, "document_id": document["id"], "document_name": document["filename"]}
                for chunk in hybrid_search(document["id"], req.question, query_embedding=query_embedding)
            ]

        if len(documents) == 1:
            chunks = search(documents[0])
        else:
            with ThreadPoolExecutor(max_workers=min(len(documents), 8)) as pool:
                chunks = [chunk for group in pool.map(search, documents) for chunk in group]
        chunks.sort(key=lambda chunk: chunk["similarity_score"], reverse=True)
        chunks = chunks[:5]
        average = sum(chunk["similarity_score"] for chunk in chunks) / len(chunks) if chunks else None
        db.log_query(req.document_id, req.question, average)
        result = generate_answer(req.question, chunks, history)
        db.save_message(conversation_id, "user", req.question)
        db.save_message(conversation_id, "assistant", result["answer"], result["cited_chunk_ids"])
        chunk_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
        sources = [SourceOut(chunk_id=chunk_id, text=chunk_by_id[chunk_id]["text"], page_number=chunk_by_id[chunk_id].get("page_number"), similarity_score=chunk_by_id[chunk_id]["similarity_score"], document_id=chunk_by_id[chunk_id]["document_id"], document_name=chunk_by_id[chunk_id]["document_name"], verification="text") for chunk_id in result["cited_chunk_ids"] if chunk_id in chunk_by_id]
        return ChatResponseOut(conversation_id=conversation_id, answer=result["answer"], sources=sources, verification="text")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat request failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
