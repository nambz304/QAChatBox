"""
Document processing pipeline:
  file bytes → extract text → chunk → list[{id, text, metadata}]

Supported formats: PDF, DOCX, TXT, MD
"""
import hashlib
import io
from pathlib import Path

from loguru import logger


# ── Public API ────────────────────────────────────────────────

def process_file(filename: str, file_bytes: bytes) -> list[dict]:
    """
    Main entry point. Extracts text from file, chunks it,
    and returns a list ready to be upserted into ChromaDB.
    """
    ext = Path(filename).suffix.lower()

    extractors = {
        ".pdf":  _extract_pdf,
        ".docx": _extract_docx,
        ".txt":  _extract_text,
        ".md":   _extract_text,
    }

    if ext not in extractors:
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {list(extractors)}")

    text = extractors[ext](file_bytes)
    if not text.strip():
        raise ValueError(f"Could not extract any text from '{filename}'")

    chunks = _chunk_text(text)
    file_hash = hashlib.md5(file_bytes).hexdigest()[:8]

    result = [
        {
            "id": f"{file_hash}_{i:04d}",
            "text": chunk,
            "metadata": {
                "filename": filename,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "file_hash": file_hash,
            },
        }
        for i, chunk in enumerate(chunks)
    ]

    logger.info(f"Processed '{filename}' → {len(result)} chunks")
    return result


# ── Text extractors ───────────────────────────────────────────

def _extract_pdf(file_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page_num, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"[Page {page_num}]\n{text}")
    return "\n\n".join(pages)


def _extract_docx(file_bytes: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _extract_text(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8", errors="replace")


# ── Chunker ───────────────────────────────────────────────────

def _chunk_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 50,
) -> list[str]:
    """
    Word-based chunker with sliding window overlap.
    chunk_size / overlap are in words (not tokens).
    512 words ≈ 640 tokens — safe for most LLM context windows.
    """
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(words):
            break
        start += chunk_size - overlap

    return chunks
