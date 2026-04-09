"""
PostgreSQL layer — 7 tables:
  employees           : seeded from data/employees.csv
  users               : login accounts (admin + employees)
  conversation_history: per-session chat turns (+ latency/token columns)
  documents           : metadata of indexed files
  feedback            : thumbs up/down + optional comment per assistant message
  llm_judge_results   : LLM-as-judge scores per assistant message
  monitoring_cache    : key-value cache for expensive metrics (RAGAS)
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS monitoring_cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Live migrations — safe on existing DBs (IF NOT EXISTS guard)
            cur.execute("""
                ALTER TABLE conversation_history
                ADD COLUMN IF NOT EXISTS response_time_ms INTEGER
            """)
            cur.execute("""
                ALTER TABLE conversation_history
                ADD COLUMN IF NOT EXISTS input_tokens INTEGER
            """)
            cur.execute("""
                ALTER TABLE conversation_history
                ADD COLUMN IF NOT EXISTS output_tokens INTEGER
            """)
            cur.execute("""
                ALTER TABLE feedback
                ADD COLUMN IF NOT EXISTS comment TEXT
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
    response_time_ms: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
) -> Optional[int]:
    """Persist a message and return its new row ID."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversation_history
                   (session_id, role, content, citations, tool_used, context_used,
                    created_at, response_time_ms, input_tokens, output_tokens)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (
                    session_id, role, content,
                    json.dumps(citations or []), tool_used, context_used,
                    datetime.now().isoformat(),
                    response_time_ms, input_tokens, output_tokens,
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

def save_feedback(session_id: str, message_id: int, rating: int,
                  comment: Optional[str] = None) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO feedback (session_id, message_id, rating, comment)
                   VALUES (%s, %s, %s, %s)""",
                (session_id, message_id, rating, comment or None),
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


# ── Dashboard queries ──────────────────────────────────────────

def get_dashboard_kpis(days: int = 7) -> dict:
    """Aggregate KPIs: latency, tokens, request count, feedback ratio."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_requests,
                    ROUND(AVG(response_time_ms)) AS avg_latency_ms,
                    ROUND(AVG(input_tokens))     AS avg_input_tokens,
                    ROUND(AVG(output_tokens))    AS avg_output_tokens,
                    SUM(COALESCE(input_tokens, 0))  AS total_input_tokens,
                    SUM(COALESCE(output_tokens, 0)) AS total_output_tokens
                FROM conversation_history
                WHERE role = 'assistant'
                  AND tool_used NOT IN ('', 'clarification')
                  AND created_at::TIMESTAMP >= NOW() - INTERVAL '1 day' * %s
                """,
                (days,),
            )
            perf = cur.fetchone() or {}

            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE rating = 1)  AS positive,
                    COUNT(*) AS total
                FROM feedback
                WHERE created_at >= NOW() - INTERVAL '1 day' * %s
                """,
                (days,),
            )
            fb = cur.fetchone() or {}

    total = int(fb.get("total") or 0)
    positive = int(fb.get("positive") or 0)
    positive_pct = round(positive / total * 100, 1) if total > 0 else 0.0

    return {
        "total_requests":    int(perf.get("total_requests") or 0),
        "avg_latency_ms":    int(perf.get("avg_latency_ms") or 0),
        "avg_input_tokens":  int(perf.get("avg_input_tokens") or 0),
        "avg_output_tokens": int(perf.get("avg_output_tokens") or 0),
        "total_input_tokens":  int(perf.get("total_input_tokens") or 0),
        "total_output_tokens": int(perf.get("total_output_tokens") or 0),
        "positive_feedback_pct": positive_pct,
        "total_feedback":        total,
    }


def get_latency_trend(days: int = 7) -> list[dict]:
    """Daily average latency trend."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    DATE(created_at::TIMESTAMP) AS day,
                    ROUND(AVG(response_time_ms)) AS avg_ms,
                    COUNT(*) AS requests
                FROM conversation_history
                WHERE role = 'assistant'
                  AND tool_used NOT IN ('', 'clarification')
                  AND response_time_ms IS NOT NULL
                  AND created_at::TIMESTAMP >= NOW() - INTERVAL '1 day' * %s
                GROUP BY day
                ORDER BY day ASC
                """,
                (days,),
            )
            rows = cur.fetchall()
    return [{"date": str(r["day"]), "avg_ms": int(r["avg_ms"] or 0),
             "requests": int(r["requests"])} for r in rows]


def get_tool_usage_breakdown(days: int = 7) -> dict:
    """Count of requests by tool."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT tool_used, COUNT(*) AS cnt
                FROM conversation_history
                WHERE role = 'assistant'
                  AND tool_used IN ('rag', 'sql')
                  AND created_at::TIMESTAMP >= NOW() - INTERVAL '1 day' * %s
                GROUP BY tool_used
                """,
                (days,),
            )
            rows = cur.fetchall()
    return {r["tool_used"]: int(r["cnt"]) for r in rows}


def get_judge_summary(days: int = 7) -> dict:
    """Average LLM judge scores and hallucination rate."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    ROUND(AVG(helpfulness)::NUMERIC, 2)   AS avg_helpfulness,
                    ROUND(AVG(factual_score)::NUMERIC, 2) AS avg_factual,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE hallucination = TRUE) AS flagged
                FROM llm_judge_results
                WHERE created_at >= NOW() - INTERVAL '1 day' * %s
                """,
                (days,),
            )
            row = cur.fetchone() or {}

            cur.execute(
                """
                SELECT j.message_id,
                       prev.content AS question,
                       j.judge_rationale
                FROM llm_judge_results j
                LEFT JOIN conversation_history ch ON ch.id = j.message_id
                LEFT JOIN LATERAL (
                    SELECT content FROM conversation_history
                    WHERE session_id = ch.session_id AND role = 'user' AND id < j.message_id
                    ORDER BY id DESC LIMIT 1
                ) prev ON TRUE
                WHERE j.hallucination = TRUE
                  AND j.created_at >= NOW() - INTERVAL '1 day' * %s
                ORDER BY j.created_at DESC LIMIT 20
                """,
                (days,),
            )
            flagged_rows = cur.fetchall()

    total = int(row.get("total") or 0)
    flagged = int(row.get("flagged") or 0)
    hallucination_pct = round(flagged / total * 100, 1) if total > 0 else 0.0

    return {
        "avg_helpfulness":   float(row.get("avg_helpfulness") or 0),
        "avg_factual":       float(row.get("avg_factual") or 0),
        "total_evaluated":   total,
        "flagged_count":     flagged,
        "hallucination_pct": hallucination_pct,
        "flagged_responses": [dict(r) for r in flagged_rows],
    }


def get_feedback_stats(days: int = 7) -> dict:
    """Feedback positive rate overall and broken down by tool (RAG/SQL only)."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    ch.tool_used,
                    COUNT(*) FILTER (WHERE f.rating = 1) AS positive,
                    COUNT(*) AS total
                FROM feedback f
                JOIN conversation_history ch ON ch.id = f.message_id
                WHERE f.created_at >= NOW() - INTERVAL '1 day' * %s
                  AND ch.tool_used IN ('rag', 'sql')
                GROUP BY ch.tool_used
                """,
                (days,),
            )
            rows = cur.fetchall()

    by_tool = {}
    for r in rows:
        pos = int(r["positive"] or 0)
        tot = int(r["total"] or 0)
        by_tool[r["tool_used"]] = {
            "positive_pct": round(pos / tot * 100, 1) if tot > 0 else 0.0,
            "total": tot,
        }
    return {"by_tool": by_tool}


def get_feedback_with_comments(days: int = 7) -> list[dict]:
    """Recent feedback rows with optional comment and the associated question."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    f.created_at,
                    f.rating,
                    f.comment,
                    (SELECT content
                     FROM   conversation_history u
                     WHERE  u.session_id = f.session_id
                       AND  u.role = 'user'
                       AND  u.id < f.message_id
                     ORDER  BY u.id DESC LIMIT 1) AS question
                FROM feedback f
                WHERE f.created_at >= NOW() - INTERVAL '1 day' * %s
                ORDER BY f.created_at DESC
                LIMIT 100
                """,
                (days,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_recent_logs(
    days: int = 7,
    tool: Optional[str] = None,
    rating: Optional[int] = None,
    limit: int = 100,
) -> list[dict]:
    """Conversation log with latency, judge score, feedback — filterable."""
    conditions = [
        "a.role = 'assistant'",
        "a.tool_used IN ('rag', 'sql')",
        "a.created_at::TIMESTAMP >= NOW() - INTERVAL '1 day' * %(days)s",
    ]
    params: dict = {"days": days, "limit": limit}

    if tool:
        conditions.append("a.tool_used = %(tool)s")
        params["tool"] = tool

    if rating is not None:
        conditions.append("f.rating = %(rating)s")
        params["rating"] = rating

    where = " AND ".join(conditions)

    query = f"""
        SELECT
            a.created_at,
            a.tool_used,
            a.response_time_ms,
            a.input_tokens,
            a.output_tokens,
            j.helpfulness,
            j.factual_score,
            f.rating,
            (SELECT content
             FROM   conversation_history u
             WHERE  u.session_id = a.session_id
               AND  u.role = 'user'
               AND  u.id < a.id
             ORDER  BY u.id DESC LIMIT 1) AS question
        FROM conversation_history a
        LEFT JOIN llm_judge_results j ON j.message_id = a.id
        LEFT JOIN feedback f ON f.message_id = a.id
        WHERE {where}
        ORDER BY a.created_at DESC
        LIMIT %(limit)s
    """

    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return [dict(r) for r in rows]


# ── Monitoring cache ───────────────────────────────────────────

def get_monitoring_cache(key: str) -> Optional[dict]:
    """Return cached value as dict, or None if not found."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT value, updated_at FROM monitoring_cache WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
    if row:
        try:
            data = json.loads(row["value"])
            data["_cached_at"] = str(row["updated_at"])
            return data
        except Exception:
            return None
    return None


def set_monitoring_cache(key: str, value: dict) -> None:
    """Upsert a cached value."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO monitoring_cache (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, json.dumps(value)),
            )
