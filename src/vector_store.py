"""
ChromaDB wrapper with sentence-transformers embeddings.
Runs fully locally — no OpenAI embedding API needed.
"""
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from loguru import logger

from .config import get_settings

settings = get_settings()
COLLECTION_NAME = "kb_documents"


class VectorStore:
    def __init__(self) -> None:
        Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=settings.chroma_path)
        self._embed_fn = SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        self._col = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"VectorStore ready — {self._col.count()} chunks in collection")

    # ── Write ─────────────────────────────────────────────────

    def add_documents(self, chunks: list[dict]) -> int:
        """
        Upsert chunks into ChromaDB.
        Each chunk must have: {id: str, text: str, metadata: dict}
        """
        if not chunks:
            return 0

        self._col.upsert(
            ids=[c["id"] for c in chunks],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
        )
        logger.info(f"Upserted {len(chunks)} chunks")
        return len(chunks)

    def delete_by_filename(self, filename: str) -> int:
        """Delete all chunks belonging to a document."""
        results = self._col.get(where={"filename": filename})
        if not results["ids"]:
            return 0
        self._col.delete(ids=results["ids"])
        logger.info(f"Deleted {len(results['ids'])} chunks for '{filename}'")
        return len(results["ids"])

    # ── Read ──────────────────────────────────────────────────

    def query(self, query_text: str, n_results: int = 5) -> list[dict]:
        """
        Semantic search. Returns list of:
          {text: str, metadata: dict, distance: float}
        Lower distance = more similar (cosine space).
        """
        total = self._col.count()
        if total == 0:
            return []

        results = self._col.query(
            query_texts=[query_text],
            n_results=min(n_results, total),
        )
        return [
            {"text": doc, "metadata": meta, "distance": dist}
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def count(self) -> int:
        return self._col.count()

    def has_filename(self, filename: str) -> bool:
        """Return True if ChromaDB has at least one chunk for this filename."""
        results = self._col.get(where={"filename": filename}, limit=1)
        return len(results["ids"]) > 0

    def list_filenames(self) -> dict[str, int]:
        """Return {filename: chunk_count} for every document in ChromaDB."""
        all_docs = self._col.get(include=["metadatas"])
        counts: dict[str, int] = {}
        for meta in all_docs["metadatas"]:
            fname = meta.get("filename", "?")
            counts[fname] = counts.get(fname, 0) + 1
        return counts


# ── Singleton ─────────────────────────────────────────────────

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
