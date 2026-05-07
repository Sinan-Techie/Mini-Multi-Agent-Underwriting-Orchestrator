"""
graph.py — builds and compiles the LangGraph StateGraph.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .state import UnderwritingState
from .nodes.supervisor import supervisor_node
from .nodes.eligibility import eligibility_node
from .nodes.screening import screening_node
from .nodes.quote import quote_node
from config import STATE_SQLITE_PATH


# ── Routing function ──────────────────────────────────────────────────────────

def route(state: UnderwritingState) -> str:
    """
    Supervisor's conditional edge — derives next node from state contents,
    never from a stored 'current_node' string.
    """
    if state.get("quote_streamed"):
        return END

    if not state.get("eligibility"):
        return "eligibility_node"

    if state.get("screening_step", 0) < len(state.get("screening_queue") or ["_", "_", "_"]):
        return "screening_node"

    return "quote_node"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph(checkpointer: AsyncSqliteSaver):
    """
    Construct and compile the underwriting StateGraph.
    Receives an already-open checkpointer from managed_checkpointer().
    """
    builder = StateGraph(UnderwritingState)

    builder.add_node("supervisor",       supervisor_node)
    builder.add_node("eligibility_node", eligibility_node)
    builder.add_node("screening_node",   screening_node)
    builder.add_node("quote_node",       quote_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route)

    builder.add_edge("eligibility_node", "supervisor")
    builder.add_edge("screening_node",   "supervisor")
    builder.add_edge("quote_node",       "supervisor")

    # No interrupt_before — we use in-node interrupt() calls instead.
    # interrupt_before fires BEFORE the node runs (empty payload).
    # In-node interrupt() fires INSIDE the node (carries our prompt payload).
    return builder.compile(checkpointer=checkpointer)


# ── Checkpointer lifecycle ────────────────────────────────────────────────────

@asynccontextmanager
async def managed_checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """
    Keeps the SQLite connection alive for the full app lifespan.

    from_conn_string() is an asynccontextmanager — the underlying
    aiosqlite connection lives only as long as we stay inside the
    'async with' block.  We must NOT exit it until the app shuts down.

    Usage in main.py lifespan:
        async with managed_checkpointer() as checkpointer:
            _graph = build_graph(checkpointer)
            yield   # app runs here; connection stays open
                    # connection closes automatically on lifespan exit
    """
    async with AsyncSqliteSaver.from_conn_string(STATE_SQLITE_PATH) as checkpointer:
        yield checkpointer