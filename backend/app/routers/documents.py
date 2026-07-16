import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile

from app import db
from app.models.schemas import DocumentOut, UploadResponse
from app.services.ingestion import ingest_document

router = APIRouter(prefix="/documents", tags=["documents"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/upload", response_model=UploadResponse)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 25MB).")
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    document_id = db.create_document(file.filename)

    dest_path = UPLOAD_DIR / f"{document_id}.pdf"
    with open(dest_path, "wb") as f:
        f.write(contents)

    def _run():
        try:
            ingest_document(str(dest_path), document_id)
        finally:
            if dest_path.exists():
                os.remove(dest_path)

    background_tasks.add_task(_run)

    return UploadResponse(document_id=document_id, status="processing")


@router.get("/{document_id}/status", response_model=DocumentOut)
def get_status(document_id: str):
    doc = db.get_document(document_id)
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
def delete_document(document_id: str):
    doc = db.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    db.delete_document(document_id)


@router.get("", response_model=list[DocumentOut])
def list_all_documents():
    docs = db.list_documents()
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
