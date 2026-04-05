"""
RAGAS evaluation module.

Fetches recent (question, answer, context) samples from conversation_history
and computes RAG quality metrics: faithfulness, answer_relevancy, context_precision.

Requires:  ragas, datasets, langchain-anthropic (all in requirements.txt)
Called by: GET /evaluate  (admin-only endpoint in api.py)
"""
import asyncio

from datasets import Dataset
from loguru import logger
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, faithfulness

from .config import get_settings
from .database import get_recent_qa_samples

settings = get_settings()


def _get_ragas_llm():
    """Wrap the Anthropic Sonnet model for use inside RAGAS metrics."""
    from langchain_anthropic import ChatAnthropic
    return LangchainLLMWrapper(
        ChatAnthropic(
            model=settings.claude_model_smart,
            api_key=settings.anthropic_api_key,
            max_tokens=1024,
        )
    )


async def run_ragas_evaluation(limit: int = 20) -> dict:
    """
    Run RAGAS evaluation on recent conversations.

    Returns a dict with:
      num_samples, faithfulness, answer_relevancy, context_precision
    """
    samples = get_recent_qa_samples(limit=limit)
    if not samples:
        return {"error": "No evaluated samples available. Have a few conversations first."}

    logger.info(f"Running RAGAS evaluation on {len(samples)} samples")

    dataset = Dataset.from_dict({
        "question": [s["question"] for s in samples],
        "answer":   [s["answer"] for s in samples],
        "contexts": [s["contexts"] for s in samples],   # list[list[str]]
    })

    # Configure all metrics to use Anthropic instead of OpenAI
    ragas_llm = _get_ragas_llm()
    metrics = [faithfulness, answer_relevancy, context_precision]
    for m in metrics:
        m.llm = ragas_llm

    # ragas.evaluate is synchronous — run in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: evaluate(dataset, metrics=metrics),
        )
    except Exception as exc:
        logger.error(f"RAGAS evaluation failed: {exc}")
        raise

    def _safe_score(key: str) -> float:
        val = result.get(key)
        if val is None:
            return 0.0
        try:
            return round(float(val), 4)
        except (TypeError, ValueError):
            return 0.0

    return {
        "num_samples":       len(samples),
        "faithfulness":      _safe_score("faithfulness"),
        "answer_relevancy":  _safe_score("answer_relevancy"),
        "context_precision": _safe_score("context_precision"),
    }
