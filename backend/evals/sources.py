"""Make sure every eval fixture is ingested, once, into the eval account.

Each fixture is stored with source_url = eval://<key>/<fingerprint>. The fingerprint covers
the file bytes and the ingestion settings, so editing a fixture or changing chunk size or
embedding model re-ingests automatically. Changes to ingestion *code* are not detected:
pass --reingest after editing ingestion.py.
"""
import hashlib
import json
from pathlib import Path

from app import db
from app.config import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS, EMBEDDING_DIM, EMBEDDING_MODEL
from app.services.ingestion import ingest_document


def find_user_id(email: str) -> str:
    for user in db.get_client().auth.admin.list_users():
        if user.email and user.email.lower() == email.lower():
            return user.id
    raise SystemExit(f"No account found for '{email}'. Sign in via the app once with that account, then re-run.")


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes())
    digest.update(json.dumps([CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS, EMBEDDING_MODEL, EMBEDDING_DIM]).encode())
    return digest.hexdigest()[:12]


def ensure_documents(keys: list[str], registry: dict[str, Path], user_id: str, *, reingest: bool = False) -> dict[str, dict]:
    existing = db.list_documents(user_id)
    resolved: dict[str, dict] = {}
    for key in keys:
        path = registry[key]
        marker = f"eval://{key}/{fingerprint(path)}"
        current = next((doc for doc in existing if doc.get("source_url") == marker and doc["status"] == "ready"), None)
        if current and not reingest:
            resolved[key] = current
            continue

        for stale in (doc for doc in existing if (doc.get("source_url") or "").startswith(f"eval://{key}/")):
            db.delete_document(stale["id"], user_id)
        print(f"  ingesting {key} ({path.name})…", flush=True)
        document_id = db.create_document(path.name, user_id, source_url=marker)
        ingest_document(str(path), document_id)
        document = db.get_document(document_id, user_id)
        if not document or document["status"] != "ready":
            raise RuntimeError(f"Ingestion failed for {key}: {(document or {}).get('error_message')}")
        resolved[key] = document
    return resolved
