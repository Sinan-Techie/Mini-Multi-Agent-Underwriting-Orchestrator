"""Supervisor router — routes each user turn to the correct agent.

Stub implementation — echoes user message, stays on current node.
"""

from typing import Callable, Awaitable
from .state import SessionState
from .auth import AuthUser
from observability import NodeTimer
from .agents import eligibility,screening, quote
from .tool_client import ToolForbiddenError, ToolUnavailableError

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
        # await send({"type": "node", "name": node})
        try:
                
            # Routing
            if node == "eligibility_agent":
                await send({
                        "type": "node",
                        "name": "eligibility_agent",
                    })
                state = await eligibility.handle(
                    user_text=user_text,
                    state=state,
                    user=user,
                    send=send,
                    trace_id=trace_id,
                )


            elif node == "health_screening_agent":
                await send({
                        "type": "node",
                        "name": "health_screening_agent",
                    })
                state = await screening.handle(
                    user_text=user_text,
                    state=state,
                    user=user,
                    send=send,
                    trace_id=trace_id,
                )


            elif node == "quote_agent":
                await send({
                        "type": "node",
                        "name": "quote_agent",
                    })
                state = await quote.handle(
                    user_text=user_text,
                    state=state,
                    user=user,
                    send=send,
                    trace_id=trace_id,
                )
            else:

                await send({
                    "type": "stream",
                    "text": f"[STUB] {node} not implemented yet.\n",
                })
        except ToolForbiddenError as exc:
            await send({
                "type": "error",
                "code": "tool_forbidden",
                "message": str(exc),
            })
            await send({"type": "done"})
            return state

        except ToolUnavailableError as exc:
            await send({
                "type": "error",
                "code": "tool_unavailable",
                "message": str(exc),
            })
            await send({"type": "done"})
            return state

        await send({"type": "done"})

    return state