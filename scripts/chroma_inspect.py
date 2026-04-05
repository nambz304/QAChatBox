"""Quick ChromaDB inspector — run via: make chroma-inspect"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.vector_store import get_vector_store

vs = get_vector_store()
col = vs._col
all_docs = col.get(include=["metadatas", "documents"])
total = len(all_docs["ids"])

print(f"\nTotal chunks: {total}")

if not total:
    print("  (empty)")
    sys.exit(0)

files: dict[str, list] = defaultdict(list)
for id_, meta, doc in zip(all_docs["ids"], all_docs["metadatas"], all_docs["documents"]):
    files[meta.get("filename", "?")].append((id_, doc))

print()
for fname, chunks in sorted(files.items()):
    print(f"  [{len(chunks)} chunks]  {fname}")
    for id_, doc in chunks:
        print(f"    {id_}: {doc[:90].strip()}...")
    print()
