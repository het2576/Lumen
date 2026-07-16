"""Pre-process a demo PDF so it's already 'ready' before a live presentation.

Usage:
    cd backend && source venv/bin/activate
    python scripts/seed_document.py /path/to/sample.pdf
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services.ingestion import ingest_document  # noqa: E402


def main(file_path: str) -> None:
    if not os.path.isfile(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    filename = os.path.basename(file_path)
    document_id = db.create_document(filename)
    print(f"Created document {document_id} for '{filename}', ingesting...")

    ingest_document(file_path, document_id)

    doc = db.get_document(document_id)
    if doc["status"] == "ready":
        print(f"Done. document_id={document_id} pages={doc['page_count']} chunks={doc['chunk_count']}")
    else:
        print(f"Ingestion failed: {doc.get('error_message')}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/seed_document.py /path/to/sample.pdf")
        sys.exit(1)
    main(sys.argv[1])
