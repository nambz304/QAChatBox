"""
Routing tests for the LangGraph agent.

These call the Anthropic API (claude-3-5-haiku), so they require
a valid ANTHROPIC_API_KEY in the environment.

Run with:  pytest tests/test_agent.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Skip entire module if no API key is set
pytestmark = pytest.mark.skipif(
    not __import__("os").getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


from src.agent import AgentState, route_question

# (query, expected_tool)
ROUTING_CASES = [
    # RAG — policy questions
    ("What is the annual leave policy?",          "rag"),
    ("How many days of sick leave do I get?",     "rag"),
    ("Tell me about the remote work policy",      "rag"),
    ("What are the rules around harassment?",     "rag"),
    ("Explain the onboarding process",            "rag"),
    ("Chinh sach nghi phep hang nam la gi?",      "rag"),   # Vietnamese
    # SQL — employee data
    ("How many employees are in Engineering?",    "sql"),
    ("What is the average salary of Senior engineers?", "sql"),
    ("List all employees in Da Nang office",      "sql"),
    ("Who has been here the longest?",            "sql"),
]


@pytest.mark.parametrize("query,expected", ROUTING_CASES)
def test_routing(query: str, expected: str):
    state = AgentState(
        messages=[],
        user_query=query,
        tool_name="",
        tool_output="",
        citations=[],
        final_answer="",
        username="test",
        role="admin",
        needs_clarification=False,
        clarification_question="",
    )
    result = route_question(state)
    assert result["tool_name"] == expected, (
        f"Query: '{query}'\n"
        f"  Expected tool: {expected}\n"
        f"  Got tool:      {result['tool_name']}"
    )
