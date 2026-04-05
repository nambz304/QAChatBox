"""
Three tools available to the LangGraph agent:

  rag_tool        — semantic search over policy documents (ChromaDB)
  sql_tool        — natural-language → SQL → execute on employee DB
  calculator_tool — safe arithmetic evaluation (no eval())
"""
import ast
import operator

from anthropic import Anthropic
from loguru import logger

from .config import get_settings
from .database import get_connection
from .vector_store import get_vector_store

settings = get_settings()
_anthropic = Anthropic(api_key=settings.anthropic_api_key)


# ═══════════════════════════════════════════════════════════════
# RAG TOOL
# ═══════════════════════════════════════════════════════════════

def rag_tool(query: str) -> dict:
    """
    Semantic search over indexed policy documents.
    Returns raw context chunks + source file names.
    """
    store = get_vector_store()

    if store.count() == 0:
        return {
            "output": (
                "No documents have been indexed yet. "
                "Please ask an admin to upload policy documents via the Admin Panel."
            ),
            "citations": [],
        }

    results = store.query(query, n_results=5)

    # Keep only relevant results; fall back to top-2 if everything is far
    relevant = [r for r in results if r["distance"] < 0.7]
    if not relevant:
        relevant = results[:2]

    context_parts: list[str] = []
    citations: list[str] = []

    for r in relevant:
        filename = r["metadata"].get("filename", "Unknown source")
        context_parts.append(f"[Source: {filename}]\n{r['text']}")
        if filename not in citations:
            citations.append(filename)

    return {
        "output": "\n\n---\n\n".join(context_parts),
        "citations": citations,
    }


# ═══════════════════════════════════════════════════════════════
# SQL TOOL
# ═══════════════════════════════════════════════════════════════

_DB_SCHEMA = """\
Table: employees
Columns:
  employee_id  TEXT  — e.g. EMP0001
  full_name    TEXT
  email        TEXT
  department   TEXT  — Engineering, Product, Design, Data, HR, Finance, Marketing, Operations
  job_title    TEXT
  level        TEXT  — Junior, Mid, Senior, Lead, Manager, Director
  salary_vnd   INTEGER — salary in Vietnamese Dong (e.g. 25000000 = 25 million VND)
  hire_date    TEXT  — YYYY-MM-DD
  manager_id   TEXT  — employee_id of their manager (nullable)
  office       TEXT  — Ha Noi, Ho Chi Minh, Da Nang, Remote
  status       TEXT  — Active, On Leave, Probation
"""

_FORBIDDEN_KEYWORDS = frozenset(
    ["INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "ATTACH"]
)


_EMPLOYEE_RESTRICTION_CLAUSE = """
IMPORTANT: This query is from a non-admin user.
You MUST NOT expose salary_vnd, email, or manager_id for specific named individuals.
Aggregate functions (AVG, COUNT, MAX by department/level) are allowed.
If the question asks for personal data (salary, email, manager) of a specific person by name or ID,
output only the word: BLOCKED
"""

_RESTRICTED_COLUMNS = frozenset({"salary_vnd", "email", "manager_id"})


def sql_tool(question: str, employee_restricted: bool = False) -> dict:
    """
    Convert a natural-language question about employees into SQL,
    execute it against the employee database, and return formatted results.
    """
    restriction = _EMPLOYEE_RESTRICTION_CLAUSE if employee_restricted else ""

    # ── Step 1: generate SQL with Claude ──────────────────────
    prompt = f"""\
You are a SQL expert for PostgreSQL. Convert the user's question into a single SELECT query.

{_DB_SCHEMA}{restriction}
Rules:
- Output ONLY the raw SQL query — no markdown, no backticks, no explanation
- Use only SELECT (never INSERT, UPDATE, DELETE, DROP)
- For salary, use AVG()/MIN()/MAX() and round to millions when helpful
- Limit result sets to 20 rows unless the user asks for everything

Question: {question}
SQL:"""

    response = _anthropic.messages.create(
        model=settings.claude_model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    sql_query = response.content[0].text.strip().rstrip(";")
    logger.debug(f"Generated SQL: {sql_query}")

    # ── Step 2: employee restriction check ────────────────────
    if employee_restricted and sql_query.strip().upper() == "BLOCKED":
        return {
            "output": "Access to individual personal data is restricted for your role.",
            "citations": ["Employee Database"],
            "sql": "BLOCKED",
        }

    # ── Step 3: safety check ──────────────────────────────────
    sql_upper = sql_query.upper()
    if any(kw in sql_upper for kw in _FORBIDDEN_KEYWORDS):
        return {
            "output": "This operation is not permitted (read-only access).",
            "citations": ["Employee Database"],
            "sql": sql_query,
        }

    # ── Step 4: execute ───────────────────────────────────────
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql_query)
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchmany(20)

        if not rows:
            return {
                "output": "No results found for this query.",
                "citations": ["Employee Database"],
                "sql": sql_query,
            }

        # ── Step 5: hard-filter restricted columns for employees ──
        if employee_restricted:
            safe_indices = [i for i, c in enumerate(columns) if c.lower() not in _RESTRICTED_COLUMNS]
            columns = [columns[i] for i in safe_indices]
            rows = [tuple(row[i] for i in safe_indices) for row in rows]

        # Format as plain-text table
        col_widths = [
            max(len(str(col)), max((len(str(row[i])) for row in rows), default=0))
            for i, col in enumerate(columns)
        ]
        sep = "  ".join("-" * w for w in col_widths)
        header = "  ".join(str(c).ljust(w) for c, w in zip(columns, col_widths))
        data_rows = [
            "  ".join(str(v).ljust(w) for v, w in zip(row, col_widths))
            for row in rows
        ]
        output = "\n".join([header, sep, *data_rows])

        return {
            "output": output,
            "citations": ["Employee Database"],
            "sql": sql_query,
        }

    except Exception as exc:
        logger.error(f"SQL error: {exc} | query: {sql_query}")
        return {
            "output": f"Database query failed: {exc}",
            "citations": [],
            "sql": sql_query,
        }


# ═══════════════════════════════════════════════════════════════
# CALCULATOR TOOL
# ═══════════════════════════════════════════════════════════════

_SAFE_OPS: dict = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.USub:     operator.neg,
    ast.UAdd:     operator.pos,
}


def _safe_eval(node: ast.AST) -> float | int:
    """Recursively evaluate an AST node using only safe arithmetic ops."""
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric constants are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if op_type is ast.Div and right == 0:
            raise ValueError("Division by zero")
        return _SAFE_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        return _SAFE_OPS[op_type](_safe_eval(node.operand))

    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


def calculator_tool(expression: str) -> dict:
    """
    Safely evaluate a math expression without using eval().
    Strips common currency symbols before parsing.
    """
    # Clean up common extras
    clean = (
        expression.strip()
        .replace(",", "")
        .replace("VND", "")
        .replace("vnđ", "")
        .replace("đ", "")
        .replace("%", "/100")
        .strip()
    )

    try:
        tree = ast.parse(clean, mode="eval")
        result = _safe_eval(tree.body)

        # Simplify floats that are whole numbers
        if isinstance(result, float) and result == int(result):
            result = int(result)

        formatted = f"{result:,}" if isinstance(result, (int, float)) else str(result)
        return {
            "output": f"{clean} = {formatted}",
            "citations": [],
        }
    except Exception as exc:
        return {
            "output": f"Could not evaluate '{clean}': {exc}",
            "citations": [],
        }
