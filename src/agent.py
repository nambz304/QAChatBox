"""
LangGraph multi-agent orchestration.

Flow:
  route_question → check_clarification
      ├─► (needs clarification) → synthesize → END  (returns clarifying question)
      ├─► rag_node  → synthesize → END
      └─► sql_node  → synthesize → END

State is a plain TypedDict — simple, serialisable, easy to debug.
Two public interfaces:
  run_agent()    — blocking, used by Slack bot
  stream_agent() — generator, yields tokens then metadata dict; used by SSE endpoint
"""
import time
from typing import Generator, Literal, Optional, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from loguru import logger

from .config import get_settings
from .database import get_history, save_message
from .tools import rag_tool, sql_tool

settings = get_settings()


# ── State ─────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: list[dict]     # [{role, content}, ...] — loaded from DB
    user_query: str
    tool_name: str           # "rag" | "sql"
    tool_output: str
    citations: list[str]
    final_answer: str
    username: str            # authenticated user (for SQL restriction)
    role: str                # "admin" | "employee"
    needs_clarification: bool
    clarification_question: str
    input_tokens: Optional[int]   # synthesis call token usage
    output_tokens: Optional[int]


# ── LLMs ──────────────────────────────────────────────────────

_llm_fast = ChatAnthropic(
    model=settings.claude_model,
    api_key=settings.anthropic_api_key,
    max_tokens=64,           # routing needs one word; clarification check needs YES/NO
)

_llm_smart = ChatAnthropic(
    model=settings.claude_model_smart,
    api_key=settings.anthropic_api_key,
    max_tokens=1024,
)


# ── Nodes ─────────────────────────────────────────────────────

def route_question(state: AgentState) -> AgentState:
    """
    Classify the query into one of two tools.
    Uses the fast/cheap model — output is a single word.
    """
    prompt = (
        "Classify the following question into exactly one category.\n\n"
        "Categories:\n"
        "  rag — company policies, rules, leave, remote work, benefits, onboarding, code of conduct\n"
        "  sql — employee data, salaries, headcount, departments, statistics about people\n\n"
        f"Question: {state['user_query']}\n\n"
        "Reply with exactly one word: rag or sql"
    )

    response = _llm_fast.invoke([HumanMessage(content=prompt)])
    raw = response.content.strip().lower().split()[0]
    tool_name = raw if raw in ("rag", "sql") else "rag"

    logger.info(f"route_question: '{state['user_query'][:60]}' → {tool_name}")
    return {**state, "tool_name": tool_name}


def rag_node(state: AgentState) -> AgentState:
    result = rag_tool(state["user_query"])
    return {**state, "tool_output": result["output"], "citations": result["citations"]}


def sql_node(state: AgentState) -> AgentState:
    result = sql_tool(state["user_query"], employee_restricted=(state.get("role") == "employee"))
    return {**state, "tool_output": result["output"], "citations": result["citations"]}


def check_clarification(state: AgentState) -> AgentState:
    """
    Determine if the user's query is too ambiguous to answer well.
    Uses the fast model with a YES/NO prompt, then generates a clarifying question if needed.
    Short-circuits for queries that are clearly specific enough to answer.
    """
    q = state["user_query"].strip()
    words = q.split()

    # Fast path 1: any query with 4+ words is specific enough
    if len(words) >= 4:
        return {**state, "needs_clarification": False, "clarification_question": ""}

    # Fast path 2: starts with a clear question/request word
    _CLEAR_STARTERS = (
        "what", "how", "who", "when", "where", "why",
        "list", "show", "tell", "give", "explain", "describe",
        "chinh", "bao", "la gi", "co bao",
    )
    if any(q.lower().startswith(s) for s in _CLEAR_STARTERS):
        return {**state, "needs_clarification": False, "clarification_question": ""}

    check_prompt = (
        "Is the following question too vague to answer — for example a bare pronoun with no "
        "context ('it', 'that', 'they'), or completely random text?\n"
        "When in doubt, answer NO. Only answer YES for genuinely unanswerable one-liners.\n\n"
        f"Question: {q}\n\n"
        "Reply with exactly one word: YES or NO"
    )
    response = _llm_fast.invoke([HumanMessage(content=check_prompt)])
    raw = response.content.strip().upper()

    if not raw.startswith("YES"):
        return {**state, "needs_clarification": False, "clarification_question": ""}

    # Generate the clarifying question using a separate call
    cq_prompt = (
        f"The user asked: '{q}'\n\n"
        "Write a single, concise clarifying question to understand what they mean. "
        "Do not answer the question — only ask for the clarification you need. "
        "Respond in the same language the user used (Vietnamese or English)."
    )
    cq_response = _llm_fast.invoke([HumanMessage(content=cq_prompt)])
    clarification_question = cq_response.content.strip()
    logger.info(f"check_clarification: needs clarification → '{clarification_question[:80]}'")

    return {**state, "needs_clarification": True, "clarification_question": clarification_question}


def synthesize(state: AgentState) -> AgentState:
    """
    Use the smart model to turn raw tool output into a helpful,
    conversational answer — taking prior history into account.
    If clarification is needed, return the clarifying question directly.
    """
    if state.get("needs_clarification"):
        return {**state, "final_answer": state["clarification_question"],
                "input_tokens": None, "output_tokens": None}
    messages = _build_synthesis_messages(state)
    response = _llm_smart.invoke(messages)
    # Extract token usage from response metadata (LangChain Anthropic)
    usage = getattr(response, "usage_metadata", None) or {}
    if not usage:
        usage = (response.response_metadata or {}).get("usage", {})
    return {
        **state,
        "final_answer":  response.content,
        "input_tokens":  usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


# ── Shared helper ─────────────────────────────────────────────

def _build_synthesis_messages(state: AgentState) -> list:
    """
    Build the LangChain message list for the synthesis step.
    Shared by both run_agent() (invoke) and stream_agent() (stream).
    """
    system = SystemMessage(content=(
        "You are a helpful internal company assistant. "
        "Answer accurately and concisely based on the provided context. "
        "Be professional but friendly. "
        "When presenting tabular data, keep it readable. "
        "If the context does not contain enough information, say so clearly. "
        "Always respond in the same language the user used (Vietnamese or English)."
    ))

    # Build message history (last 6 turns)
    lc_messages: list = [system]
    for m in state["messages"][-6:]:
        cls = HumanMessage if m["role"] == "user" else AIMessage
        lc_messages.append(cls(content=m["content"]))

    # Append current query + tool output
    context_msg = (
        f"User question: {state['user_query']}\n\n"
        f"Context retrieved via {state['tool_name'].upper()}:\n"
        f"{state['tool_output']}\n\n"
        "Please provide a clear, helpful answer based on the above context."
    )
    lc_messages.append(HumanMessage(content=context_msg))
    return lc_messages


def _run_routing_and_tools(state: AgentState) -> AgentState:
    """Run routing + tool execution synchronously (shared logic)."""
    state = route_question(state)
    node_map = {
        "rag": rag_node,
        "sql": sql_node,
    }
    node = node_map.get(state["tool_name"], rag_node)
    return node(state)


# ── Graph (used by run_agent) ──────────────────────────────────

def _build_graph():
    def _decide_after_route(
        state: AgentState,
    ) -> Literal["rag_node", "sql_node"]:
        return f"{state['tool_name']}_node"  # type: ignore[return-value]

    g = StateGraph(AgentState)

    g.add_node("route_question", route_question)
    g.add_node("rag_node",       rag_node)
    g.add_node("sql_node",       sql_node)
    g.add_node("synthesize",     synthesize)

    g.set_entry_point("route_question")
    g.add_conditional_edges("route_question", _decide_after_route)
    g.add_edge("rag_node",   "synthesize")
    g.add_edge("sql_node",   "synthesize")
    g.add_edge("synthesize", END)

    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ── Public interface ──────────────────────────────────────────

def run_agent(user_query: str, session_id: str,
              username: str = "", role: str = "employee") -> dict:
    """
    Blocking entry point — used by Slack bot.
    Loads history → runs graph → persists result → returns answer dict.
    """
    history = get_history(session_id, limit=10)

    initial_state = AgentState(
        messages=history,
        user_query=user_query,
        tool_name="",
        tool_output="",
        citations=[],
        final_answer="",
        username=username,
        role=role,
        needs_clarification=False,
        clarification_question="",
        input_tokens=None,
        output_tokens=None,
    )

    start = time.time()
    result = _get_graph().invoke(initial_state)
    response_time_ms = int((time.time() - start) * 1000)

    save_message(session_id, "user", user_query)
    msg_id = save_message(
        session_id,
        "assistant",
        result["final_answer"],
        citations=result["citations"],
        tool_used=result["tool_name"],
        context_used=result["tool_output"],
        response_time_ms=response_time_ms,
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
    )

    return {
        "answer":             result["final_answer"],
        "citations":          result["citations"],
        "tool_used":          result["tool_name"],
        "needs_clarification": False,
        "message_id":         msg_id or 0,
    }


def stream_agent(
    user_query: str,
    session_id: str,
    username: str = "",
    role: str = "employee",
) -> Generator[str | dict, None, None]:
    """
    Streaming entry point — used by the SSE /chat/stream endpoint.

    Yields:
      str  — text tokens as they arrive from Claude
      dict — final metadata item: {"citations": [...], "tool_used": "..."}
               (always the last yielded value; signals stream completion)

    Caller is responsible for saving to DB (handled in api.py after full text
    is accumulated) so this generator stays side-effect-free during streaming.
    """
    history = get_history(session_id, limit=10)

    state = AgentState(
        messages=history,
        user_query=user_query,
        tool_name="",
        tool_output="",
        citations=[],
        final_answer="",
        username=username,
        role=role,
        needs_clarification=False,
        clarification_question="",
        input_tokens=None,
        output_tokens=None,
    )

    start = time.time()

    # Step 1 & 2: routing + tool execution (fast, ~1-2 s, no streaming needed)
    state = _run_routing_and_tools(state)

    # Step 3: stream synthesis token-by-token
    messages = _build_synthesis_messages(state)
    full_answer = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    for chunk in _llm_smart.stream(messages):
        token = chunk.content
        if token:
            full_answer += token
            yield token
        # Capture token usage from the last chunk's metadata
        usage = getattr(chunk, "usage_metadata", None) or {}
        if not usage:
            usage = (getattr(chunk, "response_metadata", None) or {}).get("usage", {})
        if usage:
            input_tokens = usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("output_tokens", output_tokens)

    response_time_ms = int((time.time() - start) * 1000)

    # Persist after full answer is assembled
    save_message(session_id, "user", user_query)
    msg_id = save_message(
        session_id,
        "assistant",
        full_answer,
        citations=state["citations"],
        tool_used=state["tool_name"],
        context_used=state["tool_output"],
        response_time_ms=response_time_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    # Final metadata item — signals stream is done
    yield {
        "citations":           state["citations"],
        "tool_used":           state["tool_name"],
        "needs_clarification": False,
        "message_id":          msg_id or 0,
        "answer":              full_answer,
    }
