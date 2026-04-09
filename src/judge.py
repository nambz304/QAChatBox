"""
LLM-as-judge: asynchronously evaluates each assistant response.

Runs as a fire-and-forget background task (via FastAPI BackgroundTasks).
Results are stored in llm_judge_results and surfaced via GET /monitoring.

Uses the fast/cheap model (Haiku) to keep latency and cost low.
All exceptions are caught and logged — never propagated to the user.
"""
from anthropic import AsyncAnthropic
from loguru import logger

from .config import get_settings
from .database import save_judge_result

settings = get_settings()
_async_anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)

_JUDGE_PROMPT_RAG = """\
You are a quality evaluator for an internal HR/company knowledge base chatbot.

User question: {question}
Assistant answer: {answer}
Sources cited: {citations}

Evaluate on three dimensions:
1. Helpfulness (1-5): How useful is this answer for the user's actual need?
   1=completely unhelpful, 3=partially helpful, 5=exactly what was needed
2. Factual consistency (1-5): How well does the answer match what the cited sources would support?
   1=directly contradicts sources, 3=partially supported, 5=fully supported by citations
3. Hallucination (yes/no): Does the answer contain specific claims NOT supported by the cited sources?

Reply in this EXACT format (no extra text):
HELPFULNESS: <1-5>
FACTUAL: <1-5>
HALLUCINATION: <yes/no>
RATIONALE: <one sentence explaining the scores>"""

_JUDGE_PROMPT_SQL = """\
You are a quality evaluator for an internal HR/company knowledge base chatbot.

User question: {question}
Assistant answer: {answer}

This answer was generated from a LIVE DATABASE QUERY — it contains real employee or company data, not document citations. Do NOT penalize for missing citations.

Evaluate on three dimensions:
1. Helpfulness (1-5): How useful and complete is this answer for the user's actual need?
   1=completely unhelpful, 3=partially helpful, 5=exactly what was needed
2. Factual consistency (1-5): Does the answer directly address the question asked without adding unrelated claims?
   1=answer is completely off-topic, 3=partially on-topic, 5=directly and accurately answers the question
3. Hallucination (yes/no): Does the answer make up specific names, numbers, or facts that seem implausible or unrelated to the question?

Reply in this EXACT format (no extra text):
HELPFULNESS: <1-5>
FACTUAL: <1-5>
HALLUCINATION: <yes/no>
RATIONALE: <one sentence explaining the scores>"""


async def judge_response(
    session_id: str,
    message_id: int,
    question: str,
    answer: str,
    citations: list[str],
    tool_used: str = "rag",
) -> None:
    """
    Evaluate an assistant response asynchronously.
    Saves result to llm_judge_results table.
    Errors are logged but never raised.
    """
    try:
        if tool_used == "sql":
            prompt = _JUDGE_PROMPT_SQL.format(
                question=question[:500],
                answer=answer[:1000],
            )
        else:
            citations_str = ", ".join(citations) if citations else "none"
            prompt = _JUDGE_PROMPT_RAG.format(
                question=question[:500],
                answer=answer[:1000],
                citations=citations_str,
            )

        response = await _async_anthropic.messages.create(
            model=settings.claude_model,   # use fast/cheap model
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # Parse structured response
        lines: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                lines[key.strip().upper()] = val.strip()

        helpfulness   = _safe_int(lines.get("HELPFULNESS"), default=3, min_val=1, max_val=5)
        factual_score = _safe_int(lines.get("FACTUAL"), default=3, min_val=1, max_val=5)
        hallucination = lines.get("HALLUCINATION", "no").lower().startswith("yes")
        rationale     = lines.get("RATIONALE", "")

        save_judge_result(
            session_id=session_id,
            message_id=message_id,
            helpfulness=helpfulness,
            factual_score=factual_score,
            hallucination=hallucination,
            rationale=rationale,
        )
        logger.debug(
            f"Judge: session={session_id} msg={message_id} "
            f"help={helpfulness} factual={factual_score} hallucination={hallucination}"
        )

    except Exception as exc:
        logger.warning(f"LLM judge failed (non-fatal): session={session_id} msg={message_id}: {exc}")


def _safe_int(value: str | None, default: int, min_val: int, max_val: int) -> int:
    if value is None:
        return default
    try:
        return max(min_val, min(max_val, int(value)))
    except (ValueError, TypeError):
        return default
