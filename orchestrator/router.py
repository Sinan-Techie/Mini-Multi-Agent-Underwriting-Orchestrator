"""Supervisor router — routes each user turn to the correct agent.

Stub implementation — echoes user message, stays on current node.
"""

from typing import Callable, Awaitable
from .state import SessionState
from .auth import AuthUser
from observability import log, NodeTimer


async def handle_turn(
    *,
    user_text: str,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
    trace_id: str,
) -> SessionState:
    """
    Route the user's message to the appropriate agent based on current_node.
    Returns the (possibly mutated) state after the agent handles the turn.
    """
    node = state["current_node"]
    state["last_user_msg"] = user_text

    # This will be replaced with real agent dispatch.
    with NodeTimer(trace_id=trace_id, session_id=state["session_id"], node=node):
        await send({"type": "node", "name": node})

        # Stub response — chunked to demonstrate streaming
        stub_reply = (
            f"[STUB] You said: '{user_text}'\n"
            f"Current node: {node}\n"
            f"Role: {user.role}\n"
            f"(Agents will be wired in Phase 4)"
        )
        for chunk in _chunk(stub_reply, size=30):
            await send({"type": "stream", "text": chunk})

        await send({"type": "done"})

    return state


def _chunk(text: str, size: int = 30):
    """Split text into chunks of `size` characters for fake streaming."""
    for i in range(0, len(text), size):
        yield text[i:i + size]