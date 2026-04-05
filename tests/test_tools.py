"""
Unit tests for individual tools.
These tests do NOT call external APIs (Anthropic) — only calculator and
DB-layer tests are fully offline. RAG tests require an initialised DB.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────
# Calculator tool (no API, no DB — always runs)
# ─────────────────────────────────────────────────────────────

from src.tools import calculator_tool


@pytest.mark.parametrize("expr,expected_fragment", [
    ("2 + 2",               "4"),
    ("100 * 12",            "1,200"),
    ("25000000 * 12",       "300,000,000"),
    ("(10 + 5) * 2",        "30"),
    ("100 / 4",             "25"),
    ("2 ** 10",             "1,024"),
    ("50 / 100 * 30000000", "15,000,000"),   # 50% of 30M
])
def test_calculator_valid(expr: str, expected_fragment: str):
    result = calculator_tool(expr)
    assert expected_fragment in result["output"], (
        f"calc('{expr}') → '{result['output']}', expected '{expected_fragment}'"
    )


@pytest.mark.parametrize("expr", [
    "import os",
    "__import__('os')",
    "os.system('ls')",
    "open('/etc/passwd')",
])
def test_calculator_rejects_code_injection(expr: str):
    result = calculator_tool(expr)
    assert "Could not evaluate" in result["output"]


def test_calculator_division_by_zero():
    result = calculator_tool("1 / 0")
    assert "Could not evaluate" in result["output"]


# ─────────────────────────────────────────────────────────────
# Document processor (no API)
# ─────────────────────────────────────────────────────────────

from src.document_processor import process_file, _chunk_text


def test_process_txt():
    content = b"Hello world. " * 100
    chunks = process_file("test.txt", content)
    assert len(chunks) > 0
    assert all("id" in c and "text" in c and "metadata" in c for c in chunks)
    assert chunks[0]["metadata"]["filename"] == "test.txt"


def test_process_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported file type"):
        process_file("file.xlsx", b"data")


def test_chunk_text_overlap():
    words = ["word"] * 200
    text = " ".join(words)
    chunks = _chunk_text(text, chunk_size=100, overlap=20)
    # With 200 words, chunk_size=100, overlap=20: ~3 chunks
    assert len(chunks) >= 2


def test_chunk_empty_text():
    assert _chunk_text("") == []


# ─────────────────────────────────────────────────────────────
# Database layer (creates a temp DB)
# ─────────────────────────────────────────────────────────────

import os
import tempfile

from src.config import get_settings


def test_db_init_and_seed(tmp_path):
    """DB initialises without error and seeds admin user."""
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["ADMIN_USERNAME"] = "testadmin"
    os.environ["ADMIN_PASSWORD"] = "testpass"

    # Re-import with fresh settings
    get_settings.cache_clear()

    from src.database import init_db, verify_user
    init_db()

    user = verify_user("testadmin", "testpass")
    assert user is not None
    assert user["role"] == "admin"

    wrong = verify_user("testadmin", "wrongpass")
    assert wrong is None
