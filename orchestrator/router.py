"""Supervisor router — routes each user turn to the correct agent.

Stub implementation — echoes user message, stays on current node.
"""

from typing import Callable, Awaitable
from .state import SessionState
from .auth import AuthUser
from observability import NodeTimer
from .agents import eligibility,screening


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

    with NodeTimer(trace_id=trace_id, session_id=state["session_id"], node=node):
        await send({"type": "node", "name": node})

        if node == "eligibility_agent":
            state = await eligibility.handle(
                user_text=user_text,
                state=state,
                user=user,
                send=send,
            )
        elif node == "health_screening_agent":
                state = await screening.handle(
                    user_text=user_text,
                    state=state,
                    user=user,
                    send=send,
                )
        else:
            stub_reply = f"[STUB] {node} not implemented yet.\n"
            await send({"type": "stream", "text": stub_reply})

        await send({"type": "done"})

    return state