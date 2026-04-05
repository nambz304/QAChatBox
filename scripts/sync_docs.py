"""
Sync PostgreSQL documents table ↔ ChromaDB.

Three modes:
  detect   — report mismatches only (default / dry-run)
  fix      — detect + auto-repair without original files
  reindex  — delete then re-index policy files from disk (needs files present)

CLI usage:
  python -m scripts.sync_docs            # detect
  python -m scripts.sync_docs --fix      # fix
  python -m scripts.sync_docs --reindex  # reindex policy files
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from src.database import (
    delete_document_record,
    list_documents,
    save_document_record,
    update_document_chunk_count,
)
from src.document_processor import process_file
from src.vector_store import get_vector_store

_POLICY_FILES = [
    Path("data/leave_policy.txt"),
    Path("data/remote_work_policy.txt"),
    Path("data/code_of_conduct.txt"),
    Path("data/onboarding_guide.txt"),
]


# ── Core logic (also called from API) ────────────────────────────────────────

def detect() -> dict:
    """
    Compare DB records vs ChromaDB chunks.

    Returns:
      {
        ghosts:     [{id, filename}]        — in DB but not in ChromaDB
        orphans:    [{filename, chunk_count}] — in ChromaDB but not in DB
        mismatches: [{id, filename, db_count, chroma_count}]
        is_clean:   bool
      }
    """
    vs = get_vector_store()
    chroma_map = vs.list_filenames()          # {filename: chunk_count}
    db_docs    = list_documents()             # [{id, filename, chunk_count, ...}]

    db_by_name: dict[str, dict] = {}
    for doc in db_docs:
        db_by_name.setdefault(doc["filename"], doc)   # first record wins if duplicates

    ghosts     = []
    mismatches = []

    for doc in db_docs:
        fname = doc["filename"]
        if fname not in chroma_map:
            ghosts.append({"id": doc["id"], "filename": fname})
        elif chroma_map[fname] != doc["chunk_count"]:
            mismatches.append({
                "id":           doc["id"],
                "filename":     fname,
                "db_count":     doc["chunk_count"],
                "chroma_count": chroma_map[fname],
            })

    orphans = [
        {"filename": fname, "chunk_count": cnt}
        for fname, cnt in chroma_map.items()
        if fname not in db_by_name
    ]

    return {
        "ghosts":     ghosts,
        "orphans":    orphans,
        "mismatches": mismatches,
        "is_clean":   not (ghosts or orphans or mismatches),
    }


def fix() -> dict:
    """
    Detect then auto-repair without original files:
      - Ghost  → delete stale DB record
      - Orphan → create missing DB record
      - Mismatch → update chunk_count in DB
    """
    vs     = get_vector_store()
    result = detect()

    for ghost in result["ghosts"]:
        delete_document_record(ghost["id"])
        logger.info(f"[fix] Removed ghost DB record: {ghost['filename']} (id={ghost['id']})")

    for orphan in result["orphans"]:
        save_document_record(orphan["filename"], orphan["chunk_count"], "sync")
        logger.info(f"[fix] Created DB record for orphan: {orphan['filename']} ({orphan['chunk_count']} chunks)")

    for mm in result["mismatches"]:
        update_document_chunk_count(mm["id"], mm["chroma_count"])
        logger.info(f"[fix] Updated chunk_count for {mm['filename']}: {mm['db_count']} → {mm['chroma_count']}")

    after = detect()
    return {
        "fixed_ghosts":     len(result["ghosts"]),
        "fixed_orphans":    len(result["orphans"]),
        "fixed_mismatches": len(result["mismatches"]),
        "is_clean":         after["is_clean"],
    }


def reindex(policy_files: list[Path] | None = None) -> dict:
    """
    Delete existing chunks then re-index policy files from disk.
    Only processes files that exist on disk.
    """
    if policy_files is None:
        policy_files = _POLICY_FILES

    vs           = get_vector_store()
    chroma_map   = vs.list_filenames()
    db_docs      = list_documents()
    db_by_name   = {d["filename"]: d for d in db_docs}

    indexed = []
    skipped = []

    for path in policy_files:
        if not path.exists():
            logger.warning(f"[reindex] File not found, skipping: {path}")
            skipped.append(str(path))
            continue

        if path.name in chroma_map:
            vs.delete_by_filename(path.name)
            logger.info(f"[reindex] Cleared existing chunks for: {path.name}")

        chunks = process_file(path.name, path.read_bytes())
        count  = vs.add_documents(chunks)

        if path.name in db_by_name:
            update_document_chunk_count(db_by_name[path.name]["id"], count)
        else:
            save_document_record(path.name, count, "system")

        logger.info(f"[reindex] {path.name} → {count} chunks")
        indexed.append({"filename": path.name, "chunks": count})

    return {"indexed": indexed, "skipped": skipped, "is_clean": detect()["is_clean"]}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_detect(result: dict) -> None:
    if result["is_clean"]:
        print("\n✓ DB and ChromaDB are in sync.\n")
        return

    if result["ghosts"]:
        print(f"\n[GHOST] In DB but missing from ChromaDB ({len(result['ghosts'])}):")
        for g in result["ghosts"]:
            print(f"  id={g['id']}  {g['filename']}")

    if result["orphans"]:
        print(f"\n[ORPHAN] In ChromaDB but no DB record ({len(result['orphans'])}):")
        for o in result["orphans"]:
            print(f"  {o['filename']}  ({o['chunk_count']} chunks)")

    if result["mismatches"]:
        print(f"\n[MISMATCH] chunk_count differs ({len(result['mismatches'])}):")
        for m in result["mismatches"]:
            print(f"  id={m['id']}  {m['filename']}  DB={m['db_count']} Chroma={m['chroma_count']}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync PostgreSQL ↔ ChromaDB")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument("--fix",     action="store_true", help="Auto-repair without original files")
    group.add_argument("--reindex", action="store_true", help="Re-index policy files from disk")
    args = parser.parse_args()

    if args.fix:
        result = fix()
        print(f"\nFixed: {result['fixed_ghosts']} ghost(s), {result['fixed_orphans']} orphan(s), "
              f"{result['fixed_mismatches']} mismatch(es)")
        print("Clean after fix:", result["is_clean"])
    elif args.reindex:
        result = reindex()
        for item in result["indexed"]:
            print(f"  Indexed: {item['filename']} → {item['chunks']} chunks")
        for s in result["skipped"]:
            print(f"  Skipped (not found): {s}")
        print("Clean after reindex:", result["is_clean"])
    else:
        result = detect()
        _print_detect(result)


if __name__ == "__main__":
    main()
