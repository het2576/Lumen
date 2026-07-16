from typing import Optional

from supabase import Client, create_client

from app.config import SUPABASE_KEY, SUPABASE_URL

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL / SUPABASE_KEY are not set. Copy backend/.env.example to backend/.env and fill them in."
            )
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def create_document(filename: str) -> str:
    res = get_client().table("documents").insert({"filename": filename, "status": "processing"}).execute()
    return res.data[0]["id"]


def update_document_status(
    document_id: str,
    status: str,
    *,
    page_count: Optional[int] = None,
    chunk_count: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    payload = {"status": status}
    if page_count is not None:
        payload["page_count"] = page_count
    if chunk_count is not None:
        payload["chunk_count"] = chunk_count
    if error_message is not None:
        payload["error_message"] = error_message
    get_client().table("documents").update(payload).eq("id", document_id).execute()


def get_document(document_id: str) -> Optional[dict]:
    res = get_client().table("documents").select("*").eq("id", document_id).limit(1).execute()
    return res.data[0] if res.data else None


def list_documents() -> list[dict]:
    res = get_client().table("documents").select("*").order("uploaded_at", desc=True).execute()
    return res.data


def delete_document(document_id: str) -> None:
    get_client().table("documents").delete().eq("id", document_id).execute()


def insert_chunks(document_id: str, chunks: list[dict]) -> None:
    rows = [
        {
            "document_id": document_id,
            "chunk_text": c["text"],
            "chunk_index": c["chunk_index"],
            "page_number": c.get("page_number"),
            "embedding": c["embedding"],
        }
        for c in chunks
    ]
    batch_size = 100
    for i in range(0, len(rows), batch_size):
        get_client().table("document_chunks").insert(rows[i : i + batch_size]).execute()


def get_document_chunks(document_id: str) -> list[dict]:
    res = (
        get_client()
        .table("document_chunks")
        .select("*")
        .eq("document_id", document_id)
        .order("chunk_index")
        .execute()
    )
    return res.data


def create_conversation(document_id: str) -> str:
    res = get_client().table("conversations").insert({"document_id": document_id}).execute()
    return res.data[0]["id"]


def get_conversation(conversation_id: str) -> Optional[dict]:
    res = get_client().table("conversations").select("*").eq("id", conversation_id).limit(1).execute()
    return res.data[0] if res.data else None


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    source_chunk_ids: Optional[list[str]] = None,
) -> str:
    res = (
        get_client()
        .table("messages")
        .insert(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "source_chunk_ids": source_chunk_ids or [],
            }
        )
        .execute()
    )
    return res.data[0]["id"]


def get_conversation_history(conversation_id: str, limit: int = 10) -> list[dict]:
    res = (
        get_client()
        .table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data))


def get_all_conversation_messages(conversation_id: str) -> list[dict]:
    res = (
        get_client()
        .table("messages")
        .select("id, role, content, created_at")
        .eq("conversation_id", conversation_id)
        .order("created_at")
        .execute()
    )
    return res.data


def log_query(document_id: str, question: str, avg_similarity: Optional[float]) -> None:
    get_client().table("query_log").insert(
        {"document_id": document_id, "question": question, "avg_similarity": avg_similarity}
    ).execute()


def get_stats() -> dict:
    client = get_client()
    documents = client.table("documents").select("id", count="exact").execute()
    chunks = client.table("document_chunks").select("id", count="exact").execute()
    queries = client.table("query_log").select("id", count="exact").execute()
    recent = (
        client.table("query_log")
        .select("avg_similarity")
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
    scores = [r["avg_similarity"] for r in recent.data if r["avg_similarity"] is not None]
    avg_similarity_last_10 = sum(scores) / len(scores) if scores else None

    return {
        "total_documents": documents.count or 0,
        "total_chunks": chunks.count or 0,
        "total_queries": queries.count or 0,
        "avg_similarity_last_10": avg_similarity_last_10,
    }
