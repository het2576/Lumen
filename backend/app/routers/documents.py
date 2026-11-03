import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from postgrest.exceptions import APIError

from app import db
from app.auth import get_current_user_id
from app.models.schemas import DocumentOut, UploadResponse, UrlIngestRequest
from app.services.ingestion import validate_public_url, _parse_youtube_id
from app.worker import WORKER

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
ALLOWED_SUFFIXES = {".pdf", ".csv", ".xlsx"}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _enqueue(document_id: str, kind: str, payload: dict) -> UploadResponse:
    """Hand the source to the durable queue. If that fails, say so now rather than leaving
    the document stuck in "processing"."""
    try:
        db.enqueue_ingestion(document_id, kind, payload)
    except Exception as exc:
        logger.exception("Could not queue ingestion for %s", document_id)
        db.update_document_status(document_id, "failed", error_message="Lumen couldn't queue this source for processing. Please try again.")
        raise HTTPException(status_code=503, detail="Lumen couldn't queue this source for processing. Please try again.") from exc
    WORKER.wake()
    return UploadResponse(document_id=document_id, status="processing")


def _webpage_label(url: str) -> str:
    # A concise library label remains readable while the complete URL is stored.
    parsed = urlparse(url)
    return f"Web · {parsed.hostname or 'source'}"


def _is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname in {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}


def _youtube_label(url: str) -> str:
    try:
        video_id = _parse_youtube_id(url)
        return f"YT · {video_id}"
    except Exception:
        return "YT · video"


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile,
    user_id: str = Depends(get_current_user_id),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="Supported files: PDF, CSV, and XLSX.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 25MB).")
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Database and Storage calls block; keep them off the event loop.
    document_id = await run_in_threadpool(db.create_document, file.filename, user_id)
    # Durable storage, not local disk: the instance's disk is wiped on every restart.
    storage_path = f"{user_id}/{document_id}{suffix}"
    try:
        await run_in_threadpool(db.upload_source_file, storage_path, contents, CONTENT_TYPES[suffix])
    except Exception as exc:
        logger.exception("Could not store upload for %s", document_id)
        await run_in_threadpool(db.update_document_status, document_id, "failed", error_message="The file couldn't be stored. Please try again.")
        raise HTTPException(status_code=503, detail="The file couldn't be stored. Please try again.") from exc
    return await run_in_threadpool(_enqueue, document_id, "file", {"storage_path": storage_path, "filename": file.filename})


@router.post("/url", response_model=UploadResponse)
def add_url_source(
    payload: UrlIngestRequest,
    user_id: str = Depends(get_current_user_id),
):
    url_str = payload.url.strip()
    if _is_youtube_url(url_str):
        try:
            _parse_youtube_id(url_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        document_id = db.create_document(_youtube_label(url_str), user_id, source_url=url_str)
        return _enqueue(document_id, "youtube", {"url": url_str})

    try:
        url = validate_public_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    document_id = db.create_document(_webpage_label(url), user_id, source_url=url)
    return _enqueue(document_id, "webpage", {"url": url})


@router.post("/youtube", response_model=UploadResponse)
def add_youtube_source(
    payload: UrlIngestRequest,
    user_id: str = Depends(get_current_user_id),
):
    url = payload.url.strip()
    if not _is_youtube_url(url):
        raise HTTPException(status_code=400, detail="That doesn't look like a YouTube URL. Paste a youtube.com or youtu.be link.")
    try:
        _parse_youtube_id(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    document_id = db.create_document(_youtube_label(url), user_id, source_url=url)
    return _enqueue(document_id, "youtube", {"url": url})


@router.get("/{document_id}/status", response_model=DocumentOut)
def get_status(document_id: str, user_id: str = Depends(get_current_user_id)):
    doc = db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentOut(
        id=doc["id"],
        filename=doc["filename"],
        uploaded_at=doc["uploaded_at"],
        status=doc["status"],
        page_count=doc.get("page_count"),
        chunk_count=doc.get("chunk_count"),
        error_message=doc.get("error_message"),
    )


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, user_id: str = Depends(get_current_user_id)):
    doc = db.get_document(document_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    # A source deleted before it was processed still has its upload in Storage.
    try:
        for storage_path in db.pending_source_files(document_id):
            db.remove_source_file(storage_path)
    except Exception:
        logger.warning("Could not remove stored upload for %s", document_id, exc_info=True)
    try:
        db.delete_document(document_id, user_id)
    except APIError as e:
        raise HTTPException(
            status_code=409,
            detail="This document couldn't be deleted because of a database constraint. Run the cascade fix in backend/db/schema.sql against your Supabase project and try again.",
        ) from e


@router.get("", response_model=list[DocumentOut])
def list_all_documents(user_id: str = Depends(get_current_user_id)):
    docs = db.list_documents(user_id)
    return [
        DocumentOut(
            id=d["id"],
            filename=d["filename"],
            uploaded_at=d["uploaded_at"],
            status=d["status"],
            page_count=d.get("page_count"),
            chunk_count=d.get("chunk_count"),
            error_message=d.get("error_message"),
        )
        for d in docs
    ]
