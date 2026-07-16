from fastapi import APIRouter, HTTPException

from app import db
from app.models.schemas import ChatRequest, ChatResponseOut, ConversationHistoryOut, ConversationMessageOut, SourceOut
from app.services.generation import generate_answer
from app.services.retrieval import hybrid_search

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/{conversation_id}/messages", response_model=ConversationHistoryOut)
def get_messages(conversation_id: str):
    conversation = db.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return ConversationHistoryOut(
        conversation_id=conversation_id,
        document_id=conversation["document_id"],
        messages=[ConversationMessageOut(**message) for message in db.get_all_conversation_messages(conversation_id)],
    )


@router.post("", response_model=ChatResponseOut)
def chat(req: ChatRequest):
    document = db.get_document(req.document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    if document["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Document is not ready yet (status: {document['status']}).")

    if req.conversation_id:
        conversation = db.get_conversation(req.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        conversation_id = req.conversation_id
    else:
        conversation_id = db.create_conversation(req.document_id)

    history = db.get_conversation_history(conversation_id)

    chunks = hybrid_search(req.document_id, req.question)
    avg_similarity = (
        sum(c["similarity_score"] for c in chunks) / len(chunks) if chunks else None
    )
    db.log_query(req.document_id, req.question, avg_similarity)

    result = generate_answer(req.question, chunks, history)

    db.save_message(conversation_id, "user", req.question)
    db.save_message(conversation_id, "assistant", result["answer"], result["cited_chunk_ids"])

    chunk_by_id = {c["chunk_id"]: c for c in chunks}
    sources = [
        SourceOut(
            chunk_id=cid,
            text=chunk_by_id[cid]["text"],
            page_number=chunk_by_id[cid].get("page_number"),
            similarity_score=chunk_by_id[cid]["similarity_score"],
        )
        for cid in result["cited_chunk_ids"]
        if cid in chunk_by_id
    ]

    return ChatResponseOut(conversation_id=conversation_id, answer=result["answer"], sources=sources)
