import logging
import time

import pdfplumber
import tiktoken

from app import db
from app.config import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS, EMBEDDING_DIM, EMBEDDING_MODEL
from app.services.gemini_client import get_genai

logger = logging.getLogger(__name__)

_encoder = tiktoken.get_encoding("cl100k_base")


def extract_text(file_path: str) -> list[dict]:
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"text": text, "page_number": i})
    return pages


def chunk_text(pages: list[dict]) -> list[dict]:
    chunks = []
    chunk_index = 0

    for page in pages:
        tokens = _encoder.encode(page["text"])
        start = 0
        while start < len(tokens):
            end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_str = _encoder.decode(chunk_tokens).strip()
            if chunk_str:
                chunks.append(
                    {
                        "text": chunk_str,
                        "page_number": page["page_number"],
                        "chunk_index": chunk_index,
                    }
                )
                chunk_index += 1
            if end == len(tokens):
                break
            start = end - CHUNK_OVERLAP_TOKENS

    return chunks


def embed_chunks(chunks: list[dict]) -> list[dict]:
    genai = get_genai()
    embedded = []
    for chunk in chunks:
        for attempt in range(3):
            try:
                result = genai.embed_content(
                    model=EMBEDDING_MODEL,
                    content=chunk["text"],
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBEDDING_DIM,
                )
                embedded.append({**chunk, "embedding": result["embedding"]})
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.05)
    return embedded


def ingest_document(file_path: str, document_id: str) -> None:
    try:
        pages = extract_text(file_path)
        if not pages:
            raise ValueError("No extractable text found in PDF (it may be scanned/image-only).")

        chunks = chunk_text(pages)
        if not chunks:
            raise ValueError("Text extracted but chunking produced no chunks.")

        embedded_chunks = embed_chunks(chunks)
        db.insert_chunks(document_id, embedded_chunks)

        db.update_document_status(
            document_id,
            "ready",
            page_count=len(pages),
            chunk_count=len(embedded_chunks),
        )
    except Exception as e:
        logger.exception("Ingestion failed for document %s", document_id)
        db.update_document_status(document_id, "failed", error_message=str(e)[:500])
