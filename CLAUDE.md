# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# First-time setup: seed the SQLite DB and index policy documents
python -m scripts.seed_data

# Start all services (API + UI + optional Slack bot)
python main.py

# Start individual services
python main.py --api      # FastAPI on :8000
python main.py --ui       # Streamlit on :8501
python main.py --slack    # Slack bot only

# Or run services directly
uvicorn src.api:app --reload --port 8000
streamlit run src/ui.py --server.port 8501

# Docker (recommended for prod-like setup)
docker-compose up --build

# Tests — offline (no API key needed)
pytest tests/test_tools.py -v

# Tests — routing (requires ANTHROPIC_API_KEY)
pytest tests/test_agent.py -v
```

## Architecture

This is a LangGraph-based RAG chatbot that routes questions to one of three tools depending on intent, then synthesizes an answer with Claude.

**Agent flow** (`src/agent.py`):
1. `route_question` — fast model (Haiku) classifies the query into `rag | sql | calculator` (single token output, max_tokens=32)
2. Tool node executes — `rag_node`, `sql_node`, or `calc_node`
3. `synthesize` — smart model (Sonnet) generates a conversational answer from raw tool output + last 6 turns of history

**Three tools** (`src/tools.py`):
- `rag_tool` — semantic search over ChromaDB; filters by cosine distance < 0.7, falls back to top-2
- `sql_tool` — calls Haiku to generate a SELECT query, runs a keyword safety check, executes against SQLite, returns a plain-text table
- `calculator_tool` — AST-based safe evaluator, no `eval()`, strips VND/% symbols before parsing

**Two public entry points on the agent**:
- `run_agent()` — blocking, used by Slack bot
- `stream_agent()` — generator yielding string tokens then a final `dict` with metadata; used by the FastAPI SSE endpoint (`/chat/stream`)

**Storage**:
- `data/kb.db` — SQLite with 4 tables: `employees`, `users`, `conversation_history`, `documents`
- `data/chroma_db/` — ChromaDB vector store, collection `kb_documents`, cosine space, `all-MiniLM-L6-v2` embeddings (local, no API key)

**Configuration** (`src/config.py`): all settings via `pydantic-settings` from `.env`. `get_settings()` is `@lru_cache` — call `get_settings.cache_clear()` in tests when overriding env vars.

**API** (`src/api.py`): FastAPI. The `/chat/stream` endpoint returns Server-Sent Events; the Streamlit UI (`src/ui.py`) calls this via `httpx`. Admin-only endpoints handle document upload/delete which call both `document_processor.py` and the vector store.

**Slack** (`src/slack_bot.py`): Socket Mode — no public URL needed. Only starts if both `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` are set.

**Seeding is idempotent** — `scripts/seed_data.py` (and `init_db()`) check for existing rows before inserting, so re-running is safe. `main.py` always calls seed on startup.
