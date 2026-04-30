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
from observability import new_trace_id, log

app = FastAPI(title="Underwriting Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket helpers

async def ws_send(websocket: WebSocket, event: dict) -> None:
    """Send a JSON event to the client."""
    await websocket.send_json(event)

async def _send_resume_prompt(websocket: WebSocket, state: SessionState, user: AuthUser, send: Callable[[dict], Awaitable[None]], trace_id: str) -> SessionState:
    """After a resume, re-send the question the user was last asked."""
    node = state["current_node"]

    if node == "health_screening_agent":
        if state.get("awaiting_answer") and state.get("current_question"):
            from .agents.screening import QUESTIONS
            q = QUESTIONS.get(state["current_question"])
            if q:
                await ws_send(websocket, {"type": "node", "name": node})
                await ws_send(websocket, {"type": "stream", "text": q + "\n"})
                await ws_send(websocket, {"type": "done"})

    elif node == "eligibility_agent":
        if not state["eligibility"]:
            await ws_send(websocket, {"type": "node", "name": node})
            await ws_send(websocket, {
                "type": "stream",
                "text": "Please provide your age and region (UAE, KSA, IND).\nExample: 30 IND\n",
            })
            await ws_send(websocket, {"type": "done"})

    elif node == "quote_agent":
        await ws_send(websocket, {"type": "node", "name": node})
        state = await handle(
            state=state,
            user=user,
            send=send,
            trace_id=trace_id,
        )
        await ws_send(websocket, {"type": "done"})

    return state

async def ws_auth_handshake(websocket: WebSocket) -> AuthUser:
    """
    Wait for the first message from the client.
    Expects: {"type": "auth", "token": "<jwt>"}
    Returns an AuthUser on success.
    Sends auth_failed and closes the socket on failure.
    Raises WebSocketDisconnect if the client disconnects before sending.
    """
    raw = await websocket.receive_json()

    if raw.get("type") != "auth" or not raw.get("token"):
        await ws_send(websocket, {
            "type": "auth_failed",
            "message": "first message must be {type: 'auth', token: '...'}"
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

    async def send(event: dict, _ws=websocket):
        await _ws.send_json(event)

    # On reconnect
    await ws_send(websocket, {
        "type": "node",
        "name": state["current_node"],
    })

    if state["current_node"] == "eligibility_agent" and state["last_user_msg"] == "":
        await ws_send(websocket, {
            "type": "stream",
            "text": "Welcome! Please provide your age and region (UAE, KSA, IND).\nExample: 30 IND\n",
        })
        await ws_send(websocket, {"type": "done"})

    if is_resume:
        await ws_send(websocket, {
            "type": "stream",
            "text": "Welcome back! Resuming your session.\n",
        })
        await ws_send(websocket, {"type": "done"})

        # Re-prompt the pending question so user knows what to answer
        resume_trace_id = new_trace_id()
        state = await _send_resume_prompt(websocket, state, user, send, resume_trace_id)
        save_state(user.session_id, state)

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


# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator"}