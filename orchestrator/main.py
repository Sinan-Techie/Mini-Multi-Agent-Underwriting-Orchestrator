"""FastAPI orchestrator — exposes /ws/chat.
Handles WebSocket connections, authenticates users via JWT and
maintains session state.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import Callable, Awaitable
from .auth import verify_token, AuthError, AuthUser
from .state import load_state, new_state, save_state, SessionState
from .router import handle_turn
from .agents.quote import handle
from .tool_client import ToolForbiddenError, ToolUnavailableError
from observability import new_trace_id, log

app = FastAPI(title="Mini Multi-Agent Underwriting Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



# WebSocket helpers

async def ws_send(websocket: WebSocket, event: dict) -> bool:
    """
    Send a JSON event to the client.
    Returns False if the socket is already closed, True on success.
    """
    try:
        await websocket.send_json(event)
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


def make_send(websocket: WebSocket) -> Callable[[dict], Awaitable[None]]:
    """
    Returns a send() callable for agents.
    """
    async def send(event: dict) -> None:
        try:
            await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            raise WebSocketDisconnect(code=1006)
    return send


async def _send_resume_prompt(
    websocket: WebSocket,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
    trace_id: str,
) -> tuple[SessionState, bool]:
    """
    After a resume, re-send the question the user was last asked.
    Returns (state, still_connected).

    Handles all error types that can arise:
      - WebSocketDisconnect — client left during resume, exit cleanly.
      - ToolForbiddenError  — emit tool_forbidden error frame.
      - ToolUnavailableError — emit tool_unavailable error frame.
    """
    node = state["current_node"]

    if node == "health_screening_agent":
        if state.get("awaiting_answer") and state.get("current_question"):
            from .agents.screening import QUESTIONS
            q = QUESTIONS.get(state["current_question"])
            if q:
                if not await ws_send(websocket, {"type": "stream", "text": q + "\n"}):
                    return state, False
                if not await ws_send(websocket, {"type": "done"}):
                    return state, False

    elif node == "eligibility_agent":
        if not state["eligibility"]:
            if not await ws_send(websocket, {
                "type": "stream",
                "text": "Please provide your age and region (UAE, KSA, IND).\nExample: 30 IND\n",
            }):
                return state, False
            if not await ws_send(websocket, {"type": "done"}):
                return state, False

    elif node == "quote_agent":

        try:
            state = await handle(
                state=state,
                user=user,
                send=send,
                trace_id=trace_id,
            )

        except WebSocketDisconnect:
            return state, False
        
        except ToolForbiddenError as exc:
            await ws_send(websocket, {
                "type": "error",
                "code": "tool_forbidden",
                "message": str(exc),
            })

            await ws_send(websocket, {"type": "done"})
            return state, True   
        
        except ToolUnavailableError as exc:
            await ws_send(websocket, {
                "type": "error",
                "code": "tool_unavailable",
                "message": str(exc),
            })

            await ws_send(websocket, {"type": "done"})

            return state, True  

        if not await ws_send(websocket, {"type": "done"}):
            return state, False

    return state, True


async def ws_auth_handshake(websocket: WebSocket) -> AuthUser:
    """
    Wait for the first message from the client.
    Returns an AuthUser on success.
    Sends auth_failed and closes the socket on failure.
    """
    raw = await websocket.receive_json()

    if raw.get("type") != "auth" or not raw.get("token"):
        await ws_send(websocket, {
            "type": "auth_failed",
            "message": "first message must be {type: 'auth', token: '...'}",
        })

        await websocket.close(code=1008)
        raise AuthError("malformed auth message")

    try:
        user = verify_token(raw["token"])
    except AuthError as exc:
        await ws_send(websocket, {
            "type": "auth_failed",
            "message": str(exc),
        })

        await websocket.close(code=1008)
        raise

    return user



# Main WebSocket endpoint

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()

    # Auth handshake
    try:
        user = await ws_auth_handshake(websocket)
    except (AuthError, WebSocketDisconnect):
        return

    await ws_send(websocket, {
        "type": "auth_success",
        "user_id": user.sub,
        "role": user.role,
        "session_id": user.session_id,
    })

    log(
        trace_id=new_trace_id(),
        session_id=user.session_id,
        node="orchestrator",
        event="auth_success",
        extra={"user_id": user.sub, "role": user.role},
    )

    state = load_state(user.session_id)
    is_resume = state is not None

    if state is None:
        state = new_state(user.session_id, user.role)

    send = make_send(websocket)

    if not await ws_send(websocket, {"type": "node", "name": state["current_node"]}):
        return

    # welcome prompt
    if state["current_node"] == "eligibility_agent" and state["last_user_msg"] == "":
        if not await ws_send(websocket, {
            "type": "stream",
            "text": "Welcome! Please provide your age and region (UAE, KSA, IND).\nExample: 30 IND\n",
        }):
            return
        
        if not await ws_send(websocket, {"type": "done"}):
            return

    # Resume path
    if is_resume:
        if not await ws_send(websocket, {
            "type": "stream",
            "text": "Welcome back! Resuming your session.\n",
        }):
            return
        if not await ws_send(websocket, {"type": "done"}):
            return

        resume_trace_id = new_trace_id()
        state, still_connected = await _send_resume_prompt(
            websocket, state, user, send, resume_trace_id
        )
        save_state(user.session_id, state)

        if not still_connected:
            return


    # Message loop

    while True:
        try:
            raw = await websocket.receive_json()
        except WebSocketDisconnect:
            log(
                trace_id=new_trace_id(),
                session_id=user.session_id,
                node="orchestrator",
                event="disconnect",
            )
            break

        if raw.get("type") != "message" or not raw.get("text", "").strip():
            await ws_send(websocket, {
                "type": "error",
                "code": "internal",
                "message": "expected {type: 'message', text: '...'}",
            })
            continue

        user_text = raw["text"].strip()
        trace_id = new_trace_id()

        try:
            state = await handle_turn(
                user_text=user_text,
                state=state,
                user=user,
                send=send,
                trace_id=trace_id,
            )
        except WebSocketDisconnect:
            log(
                trace_id=trace_id,
                session_id=user.session_id,
                node="orchestrator",
                event="disconnect",
            )
            break
        except Exception as exc:
            log(
                trace_id=trace_id,
                session_id=user.session_id,
                node="orchestrator",
                event="error",
                extra={"error": str(exc)},
            )
            await ws_send(websocket, {
                "type": "error",
                "code": "internal",
                "message": "unexpected server error",
            })
            await ws_send(websocket, {"type": "done"})
            continue

        save_state(user.session_id, state)



# Health check

@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}