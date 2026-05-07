"""
nodes/supervisor.py

The supervisor node itself does nothing — it is a pass-through.
All routing logic lives in graph.py's `route()` conditional edge function,
which LangGraph calls AFTER this node returns.

This separation keeps concerns clean:
  - supervisor_node  → pure state pass-through (no I/O, no mutations)
  - route()          → decides the next node based on state
  - agent nodes      → do the actual work and mutate state

Why have a supervisor node at all if it does nothing?
  Having an explicit supervisor node makes the graph topology readable and
  gives us a single place to add cross-cutting logic later (e.g. logging
  every routing decision, injecting auth context, enforcing invariants)
  without touching the agent nodes.
"""

from ..state import UnderwritingState


async def supervisor_node(state: UnderwritingState) -> dict:
    """
    Pass-through node.  Returns nothing — the state is unchanged.
    LangGraph's conditional edge (route()) decides what comes next.
    """
    return {}