"""
PostgreSQL layer — 6 tables:
  employees           : seeded from data/employees.csv
  users               : login accounts (admin + employees)
  conversation_history: per-session chat turns
  documents           : metadata of indexed files
  feedback            : thumbs up/down per assistant message
  llm_judge_results   : LLM-as-judge scores per assistant message
"""
import csv
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import bcrypt
import psycopg2
import psycopg2.extras
import psycopg2.pool
from loguru import logger

from .config import get_settings

settings = get_settings()

# Connection pool — shared across all requests
_pool: Optional[psycopg2.pool.SimpleConnectionPool] = None


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=settings.database_url,
        )
        logger.info("PostgreSQL connection pool created")
    return _pool


@contextmanager
def get_connection():
    """Borrow a connection from the pool, commit on exit, rollback on error."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── Schema & seed ─────────────────────────────────────────────

def init_db() -> None:
    """Create all tables and seed initial data if not already present."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    employee_id  TEXT PRIMARY KEY,
                    full_name    TEXT NOT NULL,
                    email        TEXT UNIQUE NOT NULL,
                    department   TEXT NOT NULL,
                    job_title    TEXT NOT NULL,
                    level        TEXT NOT NULL,
                    salary_vnd   INTEGER NOT NULL,
                    hire_date    TEXT NOT NULL,
                    manager_id   TEXT,
                    office       TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'Active'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    username      TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'employee',
                    created_at    TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id           SERIAL PRIMARY KEY,
                    session_id   TEXT NOT NULL,
                    role         TEXT NOT NULL,
                    content      TEXT NOT NULL,
                    citations    TEXT DEFAULT '[]',
                    tool_used    TEXT DEFAULT '',
                    context_used TEXT DEFAULT '',
                    created_at   TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id          SERIAL PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    uploaded_by TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id         SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    rating     SMALLINT NOT NULL CHECK (rating IN (1, -1)),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS llm_judge_results (
                    id              SERIAL PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    message_id      INTEGER NOT NULL,
                    helpfulness     SMALLINT,
                    factual_score   SMALLINT,
                    hallucination   BOOLEAN,
                    judge_rationale TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Indexes
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_session
                    ON conversation_history(session_id, created_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_emp_dept ON employees(department)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_emp_level ON employees(level)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_session ON feedback(session_id)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_judge_session ON llm_judge_results(session_id)
            """)

    logger.info("Database schema ready")
    _seed_employees()
    _seed_admin_user()
    _seed_employee_user()
    _seed_slack_service_user()


def _seed_employees() -> None:
    csv_path = Path("data/employees.csv")
    if not csv_path.exists():
        logger.warning("data/employees.csv not found — skipping employee seed")
        return

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM employees")
            if cur.fetchone()[0] > 0:
                return  # already seeded

        with open(csv_path, encoding="utf-8") as f:
            rows = [
                (
                    r["employee_id"], r["full_name"], r["email"],
                    r["department"], r["job_title"], r["level"],
                    int(r["salary_vnd"]), r["hire_date"],
                    r["manager_id"] or None, r["office"], r["status"],
                )
                for r in csv.DictReader(f)
            ]

        with get_connection() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO employees
                       (employee_id, full_name, email, department, job_title, level,
                        salary_vnd, hire_date, manager_id, office, status)
                       VALUES %s ON CONFLICT DO NOTHING""",
                    rows,
                )
        logger.info(f"Seeded {len(rows)} employees")


def _seed_admin_user() -> None:
    pw_hash = bcrypt.hashpw(
        settings.admin_password.encode(), bcrypt.gensalt()
    ).decode()

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s", (settings.admin_username,))
            exists = cur.fetchone()

            if exists:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE username = %s",
                    (pw_hash, settings.admin_username),
                )
                logger.info(f"Admin password synced from .env for '{settings.admin_username}'")
            else:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s,%s,'admin',%s)",
                    (settings.admin_username, pw_hash, datetime.now().isoformat()),
                )
                logger.info(f"Admin user '{settings.admin_username}' created")


def _seed_employee_user() -> None:
    pw_hash = bcrypt.hashpw(
        settings.employee_password.encode(), bcrypt.gensalt()
    ).decode()

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s", (settings.employee_username,))
            exists = cur.fetchone()

            if exists:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE username = %s",
                    (pw_hash, settings.employee_username),
                )
                logger.info(f"Employee password synced for '{settings.employee_username}'")
            else:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s,%s,'employee',%s)",
                    (settings.employee_username, pw_hash, datetime.now().isoformat()),
                )
                logger.info(f"Employee user '{settings.employee_username}' created")


def _seed_slack_service_user() -> None:
    pw_hash = bcrypt.hashpw(
        settings.slack_service_password.encode(), bcrypt.gensalt()
    ).decode()

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s", (settings.slack_service_username,))
            exists = cur.fetchone()

            if exists:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE username = %s",
                    (pw_hash, settings.slack_service_username),
                )
            else:
                cur.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s,%s,'admin',%s)",
                    (settings.slack_service_username, pw_hash, datetime.now().isoformat()),
                )
                logger.info(f"Slack service user '{settings.slack_service_username}' created")


# ── Auth ──────────────────────────────────────────────────────

def verify_user(username: str, password: str) -> Optional[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT username, password_hash, role FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    if row and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return {"username": row["username"], "role": row["role"]}
    return None


# ── Conversation history ──────────────────────────────────────

def save_message(
    session_id: str,
    role: str,
    content: str,
    citations: Optional[list] = None,
    tool_used: str = "",
    context_used: str = "",
) -> Optional[int]:
    """Persist a message and return its new row ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_history
                   (session_id, role, content, citations, tool_used, context_used, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (
                    session_id, role, content,
                    json.dumps(citations or []), tool_used, context_used,
                    datetime.now().isoformat(),
                ),
            )
            row = cur.fetchone()
    return row[0] if row else None


def get_history(session_id: str, limit: int = 10) -> list[dict]:
    """Return last N turns as {role, content} — used for agent context."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT role, content FROM conversation_history
                   WHERE session_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (session_id, limit),
            )
            rows = cur.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def get_full_history(session_id: str) -> list[dict]:
    """Return full history with citations — used for UI display."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, role, content, citations, tool_used, created_at
                   FROM conversation_history
                   WHERE session_id = %s
                   ORDER BY created_at ASC""",
                (session_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "id":         r["id"],
            "role":       r["role"],
            "content":    r["content"],
            "citations":  json.loads(r["citations"] or "[]"),
            "tool_used":  r["tool_used"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def delete_history(session_id: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM conversation_history WHERE session_id = %s", (session_id,)
            )


def list_user_sessions(username: str) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    session_id,
                    MIN(created_at)  AS started_at,
                    MAX(created_at)  AS last_active,
                    COUNT(*)         AS message_count,
                    (SELECT content
                     FROM   conversation_history c2
                     WHERE  c2.session_id = c1.session_id AND c2.role = 'user'
                     ORDER  BY created_at ASC LIMIT 1) AS first_message
                FROM  conversation_history c1
                WHERE session_id LIKE %s
                GROUP BY session_id
                ORDER BY last_active DESC
                LIMIT 30
                """,
                (f"web_{username}%",),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Documents ─────────────────────────────────────────────────

def save_document_record(filename: str, chunk_count: int, uploaded_by: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO documents (filename, chunk_count, uploaded_by, uploaded_at)
                   VALUES (%s,%s,%s,%s)""",
                (filename, chunk_count, uploaded_by, datetime.now().isoformat()),
            )


def list_documents() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, filename, chunk_count, uploaded_by, uploaded_at FROM documents ORDER BY uploaded_at DESC"
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def delete_document_record(doc_id: int) -> Optional[str]:
    """Delete record and return filename, or None if not found."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT filename FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            if row:
                cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
    return row["filename"] if row else None


def update_document_chunk_count(doc_id: int, chunk_count: int) -> None:
    """Update the stored chunk_count for a document record."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE documents SET chunk_count = %s WHERE id = %s",
                (chunk_count, doc_id),
            )


# ── Feedback ──────────────────────────────────────────────────

def save_feedback(session_id: str, message_id: int, rating: int) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feedback (session_id, message_id, rating)
                   VALUES (%s, %s, %s)""",
                (session_id, message_id, rating),
            )


def get_feedback_summary() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT session_id, message_id, rating, created_at
                   FROM feedback ORDER BY created_at DESC LIMIT 100"""
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── RAGAS samples ─────────────────────────────────────────────

def get_recent_qa_samples(limit: int = 50) -> list[dict]:
    """
    Fetch paired (user question, assistant answer, context) from conversation_history.
    Only returns complete pairs where the assistant response has stored context.
    """
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    u.content   AS question,
                    a.content   AS answer,
                    a.context_used AS context_raw,
                    a.citations AS citations_raw
                FROM conversation_history u
                JOIN conversation_history a
                    ON  a.session_id = u.session_id
                    AND a.role = 'assistant'
                    AND a.tool_used NOT IN ('clarification', '')
                    AND a.context_used != ''
                WHERE u.role = 'user'
                  AND u.created_at < a.created_at
                ORDER BY a.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

    samples = []
    for r in rows:
        # Split stored context into list of strings (one per chunk)
        context_text = r["context_raw"] or ""
        contexts = [chunk.strip() for chunk in context_text.split("\n\n---\n\n") if chunk.strip()]
        if not contexts:
            contexts = [context_text]

        samples.append({
            "question":  r["question"],
            "answer":    r["answer"],
            "contexts":  contexts,
            "citations": json.loads(r["citations_raw"] or "[]"),
        })
    return samples


# ── LLM Judge ─────────────────────────────────────────────────

def save_judge_result(
    session_id: str,
    message_id: int,
    helpfulness: int,
    factual_score: int,
    hallucination: bool,
    rationale: str,
) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO llm_judge_results
                   (session_id, message_id, helpfulness, factual_score, hallucination, judge_rationale)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (session_id, message_id, helpfulness, factual_score, hallucination, rationale),
            )


def get_recent_judge_results(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT session_id, message_id, helpfulness, factual_score,
                          hallucination, judge_rationale, created_at
                   FROM llm_judge_results
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]
