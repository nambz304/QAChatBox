# Internal Knowledge Base Chatbot

AI-powered company assistant built with **Claude** (Anthropic) + **LangGraph** + **ChromaDB** + **PostgreSQL**.  
Employees can ask questions in natural language via **web UI** or **Slack** — the agent automatically routes to the right data source.

---

## What it does

| Question type | Data source | Example |
|---|---|---|
| Policy / HR docs | ChromaDB (RAG) | "Chính sách nghỉ phép là gì?" |
| Employee data | PostgreSQL (SQL gen) | "Average salary in Engineering?" |

---

## Tech stack

| Layer | Technology |
|---|---|
| LLM | Claude Haiku 4.5 (routing) + Claude Sonnet 4.6 (synthesis) |
| Agent | LangGraph `StateGraph` — 3 nodes: `route_question → rag/sql_node → synthesize` |
| Vector DB | ChromaDB + `paraphrase-multilingual-MiniLM-L12-v2` (local, no extra API key) |
| Relational DB | PostgreSQL |
| API | FastAPI — SSE streaming + blocking endpoints |
| Web UI | Streamlit — login, chat, admin panel, thumbs up/down feedback |
| Slack | Slack Bolt SDK (Socket Mode — no public URL needed) |
| Auth | JWT (HS256) — role-based: `admin` / `employee` |
| Quality | RAGAS evaluation + LLM-as-judge (Haiku, fire-and-forget) |
| Deploy | Docker Compose |

---

## Quick start

### Option A — Docker (recommended)

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY
docker-compose up --build
```

- Web UI → http://localhost:8501
- API docs → http://localhost:8000/docs
- Default login: **admin** / **admin123**

### Option B — Local dev (Makefile)

```bash
make install      # create .venv (Python 3.11) + install deps
make db           # start PostgreSQL container
make seed         # seed SQLite schema + index policy docs into ChromaDB
make dev          # FastAPI :8000 + Streamlit :8501 (both with auto-reload)
```

---

## All make commands

```
make install        Tạo venv (Python 3.11) và cài toàn bộ dependencies
make seed           Seed SQLite + index ChromaDB (idempotent)
make db             Khởi động postgres container
make db-stop        Dừng postgres container

make dev            Chạy API :8000 + UI :8501 (local, auto-reload)
make api            Chỉ FastAPI :8000
make ui             Chỉ Streamlit :8501
make slack          Chỉ Slack bot

make sync           Detect desync PostgreSQL ↔ ChromaDB (dry-run)
make sync-fix       Auto-fix desync (không cần file gốc)
make sync-reindex   Xóa và index lại policy files từ disk
make chroma-inspect Xem toàn bộ chunks trong ChromaDB

make test           Offline tests (không cần API key)
make test-all       Toàn bộ tests (cần ANTHROPIC_API_KEY)

make up             docker-compose up (không rebuild)
make build          docker-compose up --build
make down           Dừng và xóa containers
make logs           Xem logs app container realtime

make add pkg=<name> Thêm package vào venv + requirements.txt
make clean          Xóa venv và cache
```

---

## Agent flow

```
User query
    │
    ▼
route_question        ← Haiku, max_tokens=64
    │                   Output: "rag" | "sql"
    ├─► rag_node      ← ChromaDB semantic search (cosine < 0.7)
    └─► sql_node      ← Haiku generates SELECT, runs on PostgreSQL
    │
    ▼
synthesize            ← Sonnet, up to 1024 tokens
    │                   Responds in same language as user (VI / EN)
    ▼
Answer + citations
```

---

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/login` | — | Login → JWT |
| POST | `/chat` | user | Blocking (Slack) |
| POST | `/chat/stream` | user | SSE streaming (UI) |
| POST | `/feedback` | user | Thumbs up/down (rating ±1) |
| GET | `/history/{session_id}` | user | Conversation history |
| DELETE | `/history/{session_id}` | user | Clear session |
| GET | `/sessions` | user | List conversations |
| POST | `/ingest` | admin | Upload & index document |
| GET | `/documents` | user | List indexed documents |
| DELETE | `/documents/{id}` | admin | Delete document |
| GET | `/sync` | admin | Detect PostgreSQL ↔ ChromaDB desync |
| POST | `/sync` | admin | Fix desync (`?reindex=true` for full reindex) |
| GET | `/evaluate` | admin | Run RAGAS evaluation on recent conversations |
| GET | `/monitoring` | admin | LLM-as-judge results + hallucination flags |
| GET | `/health` | — | Health check |

---

## Quality monitoring

### LLM-as-judge (automatic)
Every assistant response is evaluated in the background by Haiku on 3 dimensions:
- **Helpfulness** (1–5)
- **Factual consistency** (1–5)
- **Hallucination** (yes/no)

Results are stored in `llm_judge_results` and surfaced via `GET /monitoring`.

### RAGAS (on-demand)
Admin calls `GET /evaluate` to compute RAG pipeline metrics on recent conversations:
- **Faithfulness** — answer grounded in retrieved context?
- **Answer relevancy** — answer addresses the question?
- **Context precision** — retrieved chunks relevant?

### User feedback
Thumbs up / thumbs down buttons appear under every assistant message in the UI.

---

## DB ↔ ChromaDB sync

The `documents` table in PostgreSQL and ChromaDB can fall out of sync (e.g. after a ChromaDB volume reset). Use:

```bash
make sync           # detect: ghosts / orphans / mismatches
make sync-fix       # auto-repair without original files
make sync-reindex   # delete + re-index policy files from disk
```

Or via API: `GET /sync` (detect) and `POST /sync` (fix).

---

## Project structure

```
autoResearchAgent/
├── src/
│   ├── agent.py              # LangGraph StateGraph orchestration
│   ├── api.py                # FastAPI endpoints
│   ├── auth.py               # JWT create/verify, role guards
│   ├── config.py             # pydantic-settings from .env
│   ├── database.py           # PostgreSQL schema, CRUD
│   ├── document_processor.py # PDF/DOCX/TXT → chunks
│   ├── evaluation.py         # RAGAS evaluation
│   ├── judge.py              # LLM-as-judge (background task)
│   ├── slack_bot.py          # Slack Bolt (Socket Mode)
│   ├── sync.py               # DB ↔ ChromaDB sync (API wrapper)
│   ├── tools.py              # rag_tool, sql_tool
│   ├── ui.py                 # Streamlit web UI
│   └── vector_store.py       # ChromaDB wrapper
├── scripts/
│   ├── chroma_inspect.py     # CLI: inspect ChromaDB contents
│   ├── seed_data.py          # Idempotent seed (DB + ChromaDB)
│   └── sync_docs.py          # CLI: detect / fix / reindex sync
├── tests/
│   ├── test_tools.py         # Unit tests (offline)
│   └── test_agent.py         # Routing tests (requires API key)
├── data/
│   ├── employees.csv
│   ├── leave_policy.txt
│   ├── remote_work_policy.txt
│   ├── code_of_conduct.txt
│   └── onboarding_guide.txt
├── Makefile
├── Dockerfile
├── docker-compose.yml
└── entrypoint.sh
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `DATABASE_URL` | No | `postgresql://kb_user:kb_pass@localhost:5432/kb_db` | PostgreSQL DSN |
| `JWT_SECRET` | No | `change-me-in-production` | JWT signing secret — **change in prod** |
| `ADMIN_PASSWORD` | No | `admin123` | Admin account password |
| `EMPLOYEE_PASSWORD` | No | `employee123` | Employee demo password |
| `SLACK_BOT_TOKEN` | No | — | Slack Bot Token (starts with `xoxb-`) |
| `SLACK_APP_TOKEN` | No | — | Slack App Token (starts with `xapp-`) |
| `CLAUDE_MODEL` | No | `claude-haiku-4-5-20251001` | Fast model (routing, judge) |
| `CLAUDE_MODEL_SMART` | No | `claude-sonnet-4-6` | Smart model (synthesis, RAGAS) |
| `EMBEDDING_MODEL` | No | `paraphrase-multilingual-MiniLM-L12-v2` | Sentence-transformers model |

---

## Slack setup

1. https://api.slack.com/apps → **Create New App** → From scratch
2. **Socket Mode** → Enable → generate App-Level Token → `SLACK_APP_TOKEN`
3. **Event Subscriptions** → bot events: `message.im`, `app_mention`
4. **OAuth & Permissions** → scopes: `chat:write`, `im:history`, `app_mentions:read`
5. **Install App** → copy Bot Token → `SLACK_BOT_TOKEN`
6. Restart — Slack bot starts automatically when both tokens are set

---

## Running tests

```bash
make test           # offline (no API key needed)
make test-all       # full suite (ANTHROPIC_API_KEY required)
```
