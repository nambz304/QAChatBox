"""
One-time seed script — run once on first startup (entrypoint.sh calls this).
Idempotent: skips anything already indexed.

Usage:
  python -m scripts.seed_data          (from project root)
  python scripts/seed_data.py          (direct)
"""
import sys
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.database import init_db, list_documents, save_document_record
from src.document_processor import process_file
from src.vector_store import get_vector_store

_POLICY_FILES = [
    Path("data/leave_policy.txt"),
    Path("data/remote_work_policy.txt"),
    Path("data/code_of_conduct.txt"),
    Path("data/onboarding_guide.txt"),
]


def seed() -> None:
    logger.info("=== Seed script starting ===")

    # 1. Ensure DB tables exist and employees are seeded
    init_db()

    # 2. Index policy documents into ChromaDB
    # Source of truth = ChromaDB (not the DB records, which can go stale)
    store = get_vector_store()
    existing_in_db = {doc["filename"] for doc in list_documents()}

    for path in _POLICY_FILES:
        if not path.exists():
            logger.warning(f"File not found, skipping: {path}")
            continue

        if store.has_filename(path.name):
            logger.info(f"Already in ChromaDB, skipping: {path.name}")
            continue

        content = path.read_bytes()
        chunks = process_file(path.name, content)
        count = store.add_documents(chunks)
        if path.name not in existing_in_db:
            save_document_record(path.name, count, "system")
        logger.info(f"Indexed '{path.name}' → {count} chunks")

    logger.info(f"Total chunks in vector store: {store.count()}")
    logger.info("=== Seed script done ===")


if __name__ == "__main__":
    seed()
