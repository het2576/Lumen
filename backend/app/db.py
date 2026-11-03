import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client, create_client

from app.config import SUPABASE_KEY, SUPABASE_URL, UPLOAD_BUCKET

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


def create_document(filename: str, user_id: str, *, source_url: Optional[str] = None) -> str:
    res = (
        get_client()
        .table("documents")
        .insert({"filename": filename, "status": "processing", "user_id": user_id, "source_url": source_url})
        .execute()
    )
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


def get_document(document_id: str, user_id: str) -> Optional[dict]:
    res = (
        get_client()
        .table("documents")
        .select("*")
        .eq("id", document_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_documents(user_id: str) -> list[dict]:
    res = (
        get_client()
        .table("documents")
        .select("*")
        .eq("user_id", user_id)
        .order("uploaded_at", desc=True)
        .execute()
    )
    return res.data


def delete_document(document_id: str, user_id: str) -> None:
    get_client().table("documents").delete().eq("id", document_id).eq("user_id", user_id).execute()


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


def insert_document_tables(document_id: str, tables: list[dict]) -> None:
    """Persist native spreadsheet/PDF tables without flattening them into chunks."""
    if not tables:
        return
    rows = [
        {
            "document_id": document_id,
            "table_name": table["table_name"],
            "page_number": table.get("page_number"),
            "column_headers": table["column_headers"],
            "row_data": table["row_data"],
            "inferred_types": table.get("inferred_types", {}),
            "source_kind": table.get("source_kind", "spreadsheet"),
            "verified": table.get("verified", True),
        }
        for table in tables
        if table.get("column_headers") and table.get("row_data")
    ]
    if not rows:
        return
    for i in range(0, len(rows), 50):
        get_client().table("document_tables").insert(rows[i : i + 50]).execute()


def _normalize_table(row: dict) -> dict:
    """Tables saved by earlier Lumen versions store JSON as text and have no table_name."""
    table = dict(row)
    for key, empty in (("column_headers", []), ("row_data", []), ("inferred_types", {})):
        value = table.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = empty
        table[key] = value if value is not None else empty
    if not table.get("table_name"):
        page = table.get("page_number")
        table["table_name"] = table.get("sheet_name") or (
            f"Page {page} · Table {(table.get('table_index') or 0) + 1}" if page else "Table"
        )
    return table


def get_document_tables(document_ids: list[str]) -> list[dict]:
    if not document_ids:
        return []
    rows = (
        get_client()
        .table("document_tables")
        .select("*")
        .in_("document_id", document_ids)
        .order("created_at")
        .execute()
        .data
    )
    return [_normalize_table(row) for row in rows]


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


def create_conversation(document_ids: list[str]) -> str:
    if not document_ids:
        raise ValueError("A conversation requires at least one document.")
    res = get_client().table("conversations").insert({"document_id": document_ids[0]}).execute()
    conversation_id = res.data[0]["id"]
    get_client().table("conversation_documents").insert(
        [{"conversation_id": conversation_id, "document_id": document_id} for document_id in document_ids]
    ).execute()
    return conversation_id


def get_conversation(conversation_id: str) -> Optional[dict]:
    res = get_client().table("conversations").select("*").eq("id", conversation_id).limit(1).execute()
    return res.data[0] if res.data else None


def get_conversation_document_ids(conversation_id: str) -> list[str]:
    rows = (
        get_client()
        .table("conversation_documents")
        .select("document_id")
        .eq("conversation_id", conversation_id)
        .execute()
        .data
    )
    # A conversation created before the migration will be backfilled by SQL;
    # this fallback also keeps it readable if the migration is being rolled out.
    if rows:
        return [row["document_id"] for row in rows]
    conversation = get_conversation(conversation_id)
    return [conversation["document_id"]] if conversation and conversation.get("document_id") else []


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


def save_turn(conversation_id: str, question: str, answer: str, source_chunk_ids: Optional[list[str]] = None) -> None:
    """Store a question and its answer in one round trip. Explicit timestamps keep the pair
    in order: rows inserted by one statement would otherwise share the same now()."""
    asked_at = datetime.now(timezone.utc)
    get_client().table("messages").insert([
        {"conversation_id": conversation_id, "role": "user", "content": question,
         "source_chunk_ids": [], "created_at": asked_at.isoformat()},
        {"conversation_id": conversation_id, "role": "assistant", "content": answer,
         "source_chunk_ids": source_chunk_ids or [], "created_at": (asked_at + timedelta(milliseconds=1)).isoformat()},
    ]).execute()


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


def get_stats(user_id: str) -> dict:
    client = get_client()
    documents = client.table("documents").select("id", count="exact").eq("user_id", user_id).execute()
    doc_ids = [d["id"] for d in documents.data]

    if not doc_ids:
        return {"total_documents": 0, "total_chunks": 0, "total_queries": 0, "avg_similarity_last_10": None}

    chunks = client.table("document_chunks").select("id", count="exact").in_("document_id", doc_ids).execute()
    queries = client.table("query_log").select("id", count="exact").in_("document_id", doc_ids).execute()
    recent = (
        client.table("query_log")
        .select("avg_similarity")
        .in_("document_id", doc_ids)
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


# --- Ingestion queue -------------------------------------------------------------------

def enqueue_ingestion(document_id: str, kind: str, payload: dict) -> str:
    res = get_client().table("ingestion_jobs").insert(
        {"document_id": document_id, "kind": kind, "payload": payload}
    ).execute()
    return res.data[0]["id"]


def claim_ingestion_job(stale_seconds: int) -> Optional[dict]:
    """Atomically take the oldest runnable job (or one whose worker stopped heartbeating)."""
    rows = get_client().rpc("claim_ingestion_job", {"p_stale_seconds": stale_seconds}).execute().data
    return rows[0] if rows else None


def heartbeat_ingestion_job(job_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    get_client().table("ingestion_jobs").update({"locked_at": now, "updated_at": now}).eq("id", job_id).eq("status", "running").execute()


def finish_ingestion_job(job_id: str, status: str, error: Optional[str] = None) -> None:
    get_client().table("ingestion_jobs").update({
        "status": status, "last_error": error, "locked_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", job_id).execute()


def retry_ingestion_job(job_id: str, error: str, delay_seconds: float) -> None:
    now = datetime.now(timezone.utc)
    get_client().table("ingestion_jobs").update({
        "status": "queued", "last_error": error, "locked_at": None, "updated_at": now.isoformat(),
        "run_after": (now + timedelta(seconds=delay_seconds)).isoformat(),
    }).eq("id", job_id).execute()


def clear_document_content(document_id: str) -> None:
    """Remove partial output of an interrupted attempt so a retry never duplicates chunks."""
    client = get_client()
    client.table("document_chunks").delete().eq("document_id", document_id).execute()
    client.table("document_tables").delete().eq("document_id", document_id).execute()


def upload_source_file(path: str, contents: bytes, content_type: str) -> None:
    get_client().storage.from_(UPLOAD_BUCKET).upload(path, contents, {"content-type": content_type, "upsert": "true"})


def download_source_file(path: str) -> bytes:
    return get_client().storage.from_(UPLOAD_BUCKET).download(path)


def remove_source_file(path: str) -> None:
    get_client().storage.from_(UPLOAD_BUCKET).remove([path])


def pending_source_files(document_id: str) -> list[str]:
    rows = get_client().table("ingestion_jobs").select("payload").eq("document_id", document_id).execute().data
    return [row["payload"]["storage_path"] for row in rows if (row.get("payload") or {}).get("storage_path")]
