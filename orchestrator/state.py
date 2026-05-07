"""
LangGraph session state.

Changes from the original hand-rolled state.py:
  - current_node removed       → LangGraph owns routing via edges
  - awaiting_answer removed    → replaced by interrupt() inside nodes
  - current_question removed   → same; the question is re-sent on resume
                                  via interrupt()'s resume value
  - session_id kept            → used as thread_id for the checkpointer
  - role kept                  → passed in graph config so nodes can read it
  - everything else unchanged  → EligibilityData, ScreeningAnswers, QuoteData
                                  are copied verbatim from the original

The SqliteSaver checkpointer replaces save_state() / load_state() entirely.
It persists the full state automatically after every node completes and
restores it when graph.invoke() is called with the same thread_id.
"""

from typing import TypedDict, Annotated
import operator


# ── Sub-schemas (identical to original) ──────────────────────────────────────

class EligibilityData(TypedDict, total=False):
    age: int
    region: str


class ScreeningAnswers(TypedDict, total=False):
    tobacco_current: bool
    preexisting_conditions: bool
    exercise_hours_per_week: float


class QuoteData(TypedDict, total=False):
    provider: str
    price: float
    all_prices: dict


# ── Main graph state ──────────────────────────────────────────────────────────

class UnderwritingState(TypedDict):
    """
    Single source of truth passed between every LangGraph node.

    LangGraph merges node return dicts into this state automatically —
    nodes only need to return the keys they changed.

    The `messages` list uses operator.add as its reducer so that each
    node can append to it without overwriting previous entries.
    """

    # Conversation input — the current user message.
    # Nodes read state["messages"][-1] to get the latest user text.
    messages: Annotated[list[str], operator.add]

    # Identity — set once from JWT at WS connect, never mutated by nodes.
    session_id: str   # sha256(sub)[:16]  — also used as thread_id
    role: str         # "applicant" | "agent" | "admin"

    # Agent data (identical shape to original state.py) ─────────────────────
    eligibility: EligibilityData
    screening_answers: ScreeningAnswers
    screening_step: int
    screening_queue: list[str]
    quote: QuoteData
    quote_streamed: bool
    quote_recommendation: str

    # Carry-through for observability ────────────────────────────────────────
    last_user_msg: str


def new_state(session_id: str, role: str) -> UnderwritingState:
    """Fresh state for a brand-new session."""
    return UnderwritingState(
        messages=[],
        session_id=session_id,
        role=role,
        eligibility=EligibilityData(),
        screening_answers=ScreeningAnswers(),
        screening_step=0,
        screening_queue=[],
        quote=QuoteData(),
        quote_streamed=False,
        quote_recommendation="",
        last_user_msg="",
    )