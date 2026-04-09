"""
RAGAS evaluation module.

Fetches recent (question, answer, context) samples from conversation_history
and computes RAG quality metrics: faithfulness, answer_relevancy, context_precision.

Requires:  ragas>=0.2, datasets, langchain-anthropic (all in requirements.txt)
Called by: GET /evaluate  (admin-only endpoint in api.py)
"""
import asyncio

from loguru import logger
from ragas import EvaluationDataset, SingleTurnSample, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, faithfulness

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


def _get_ragas_embeddings():
    """Use local sentence-transformers model — no OpenAI key needed."""
    from langchain_community.embeddings import SentenceTransformerEmbeddings
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        emb = SentenceTransformerEmbeddings(model_name=settings.embedding_model)
    return LangchainEmbeddingsWrapper(emb)


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

    # RAGAS 0.2.x API: use EvaluationDataset + SingleTurnSample
    # Column names: user_input, response, retrieved_contexts
    eval_samples = [
        SingleTurnSample(
            user_input=s["question"],
            response=s["answer"],
            retrieved_contexts=s["contexts"],
        )
        for s in samples
    ]
    dataset = EvaluationDataset(samples=eval_samples)

    # Pass LLM directly to evaluate() — not via metric.llm in 0.2.x
    ragas_llm = _get_ragas_llm()
    ragas_emb = _get_ragas_embeddings()
    # context_precision requires ground-truth reference answers — not available here
    metrics = [faithfulness, answer_relevancy]

    # ragas.evaluate is synchronous — run in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: evaluate(dataset, metrics=metrics, llm=ragas_llm,
                             embeddings=ragas_emb),
        )
    except Exception as exc:
        logger.error(f"RAGAS evaluation failed: {exc}")
        raise

    def _safe_score(key: str) -> float:
        try:
            # result._repr_dict holds pre-computed mean scores per metric
            val = result._repr_dict.get(key)
            if val is None:
                return 0.0
            return round(float(val), 4)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    return {
        "num_samples":      len(samples),
        "faithfulness":     _safe_score("faithfulness"),
        "answer_relevancy": _safe_score("answer_relevancy"),
    }
