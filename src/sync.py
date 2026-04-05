"""
Sync logic exposed as importable functions for the API.
The actual implementation lives in scripts/sync_docs.py.
"""
from scripts.sync_docs import detect, fix, reindex

__all__ = ["detect", "fix", "reindex"]
