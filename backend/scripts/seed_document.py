"""Pre-process a demo PDF so it's already 'ready' before a live presentation.

The document is assigned to a real user account (sign in via the app once first so the
account exists), since every document now belongs to a specific signed-in user.

Usage:
    cd backend && source venv/bin/activate
    python scripts/seed_document.py /path/to/sample.pdf you@example.com
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services.ingestion import ingest_document  # noqa: E402


def find_user_id(email: str) -> str:
    users = db.get_client().auth.admin.list_users()
    for user in users:
        if user.email and user.email.lower() == email.lower():
            return user.id
    print(f"No account found for '{email}'. Sign in via the app once first, then re-run this script.")
    sys.exit(1)


def main(file_path: str, email: str) -> None:
    if not os.path.isfile(file_path):
        print(f"File not found: {file_path}")
        sys.exit(1)

    user_id = find_user_id(email)

    filename = os.path.basename(file_path)
    document_id = db.create_document(filename, user_id)
    print(f"Created document {document_id} for '{filename}' (owner: {email}), ingesting...")

    ingest_document(file_path, document_id)

    doc = db.get_document(document_id, user_id)
    if doc["status"] == "ready":
        print(f"Done. document_id={document_id} pages={doc['page_count']} chunks={doc['chunk_count']}")
    else:
        print(f"Ingestion failed: {doc.get('error_message')}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/seed_document.py /path/to/sample.pdf you@example.com")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
