"""
orchestrator/main.py  (LangGraph version)

WebSocket protocol is IDENTICAL to the original — same event shapes,
same auth handshake, same error codes.  The only change is what happens
inside the message loop: instead of router.handle_turn() we call
graph.invoke() / graph.astream() and translate LangGraph events into
the existing WS event types.

Key concepts:
  - graph.invoke(Command(resume=user_text), config)
        Resumes a previously interrupted graph from where it paused.
        First call uses a normal input dict; subsequent calls use Command.

  - interrupt events in stream output
        When a node calls interrupt(), LangGraph emits an Interrupt object
        in the stream.  We read its value (which contains our prompt) and
        send {type: stream, text: prompt} + {type: done} to the client.

  - config["configurable"]
        We thread `send`, `jwt_token`, and `trace_id` through here so
        quote_node can push tokens to the WS without importing main.py.

  - thread_id = user.session_id
        Every graph.invoke call for the same user passes the same thread_id.
        The SqliteSaver checkpointer uses it to restore the interrupted state.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command, Interrupt

from .auth import verify_token, AuthError, AuthUser
from .graph import build_graph, managed_checkpointer
from .state import new_state
from .tool_client import ToolForbiddenError, ToolUnavailableError
from observability import new_trace_id, log


# ── App lifespan: build graph once, share across all WS connections ───────────

_graph = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph
    async with managed_checkpointer() as checkpointer:
        _graph = build_graph(checkpointer)
        yield   # app runs here; checkpointer connection stays open


app = FastAPI(
    title="Mini Multi-Agent Underwriting Orchestrator (LangGraph)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WS helpers (unchanged from original) ─────────────────────────────────────

async def ws_send(websocket: WebSocket, event: dict) -> bool:
    try:
        await websocket.send_json(event)
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


def make_send(websocket: WebSocket):
    async def send(event: dict) -> None:
        try:
            await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            raise WebSocketDisconnect(code=1006)
    return send


async def ws_auth_handshake(websocket: WebSocket) -> AuthUser:
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
        await ws_send(websocket, {"type": "auth_failed", "message": str(exc)})
        await websocket.close(code=1008)
        raise
    return user


# ── Core: invoke the graph for one user turn ──────────────────────────────────

async def run_turn(
    *,
    user_text: str,
    user: AuthUser,
    send,
    trace_id: str,
    is_first_turn: bool,
) -> None:
    """
    Drive one user message through the LangGraph graph.

    First turn:    graph.ainvoke(initial_state, config)
    Subsequent:    graph.ainvoke(Command(resume=user_text), config)

    We use astream with stream_mode="values" so we can intercept
    Interrupt events (questions to send to the user) and stream
    tokens from quote_node via the send() callback in config.
    """
    config = {
        "configurable": {
            "thread_id": user.session_id,
            # Pass runtime dependencies into nodes that need them
            "send":      send,
            "jwt_token": user.token,
            "trace_id":  trace_id,
        }
    }

    if is_first_turn:
        graph_input = new_state(user.session_id, user.role)
        # Inject first message so nodes can read it
        graph_input["messages"] = [user_text]
        graph_input["last_user_msg"] = user_text
    else:
        # Resume the interrupted graph with the user's answer
        graph_input = Command(resume=user_text)

    try:
        # astream with "updates" mode gives us one dict per node that ran
        async for event in _graph.astream(
            graph_input,
            config=config,
            stream_mode="updates",
        ):
            # event is {node_name: state_delta} or an Interrupt wrapper
            for node_name, value in event.items():

                # ── Interrupt: node is pausing to ask a question ──────────
                if node_name == "__interrupt__":
                    # value is a tuple of Interrupt objects
                    for interrupt_obj in value:
                        payload = interrupt_obj.value  # our dict with "node" + "prompt"
                        node_label = payload.get("node", "agent")
                        prompt_text = payload.get("prompt", "")

                        await send({"type": "node",   "name": node_label})
                        await send({"type": "stream", "text": prompt_text + "\n"})
                        # done is sent after this loop exits (below)
                    return   # WS loop will call run_turn again with next message

                # ── Normal node update: surface the node transition ───────
                elif node_name not in ("supervisor",):
                    # Map internal node names back to WS protocol names
                    node_ws_name = {
                        "eligibility_node": "eligibility_agent",
                        "screening_node":   "health_screening_agent",
                        "quote_node":       "quote_agent",
                    }.get(node_name, node_name)
                    await send({"type": "node", "name": node_ws_name})

    except ToolForbiddenError as exc:
        await send({"type": "error", "code": "tool_forbidden",  "message": str(exc)})
    except ToolUnavailableError as exc:
        await send({"type": "error", "code": "tool_unavailable", "message": str(exc)})
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        log(trace_id=trace_id, session_id=user.session_id,
            node="orchestrator", event="error", extra={"error": str(exc)})
        await send({"type": "error", "code": "internal", "message": "unexpected server error"})


# ── WebSocket endpoint ────────────────────────────────────────────────────────

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

    send = make_send(websocket)

    # Check if this is a resume by querying the checkpointer for existing state
    existing = await _graph.aget_state(
        config={"configurable": {"thread_id": user.session_id}}
    )
    is_resume = existing.values is not None and bool(existing.values)
    is_first_turn = not is_resume

    if is_resume:
        await ws_send(websocket, {"type": "stream", "text": "Welcome back! Resuming your session.\n"})

        # Re-surface the pending interrupt (the question the user was asked)
        if existing.tasks:
            for task in existing.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        payload = intr.value
                        await ws_send(websocket, {
                            "type": "node",
                            "name": payload.get("node", "agent"),
                        })
                        await ws_send(websocket, {
                            "type": "stream",
                            "text": payload.get("prompt", "") + "\n",
                        })
    else:
        # New session — send welcome and trigger first interrupt (eligibility question)
        await ws_send(websocket, {
            "type": "node",
            "name": "eligibility_agent",
        })
        # Run the graph to get the first interrupt (eligibility prompt)
        trace_id = new_trace_id()
        try:
            async for event in _graph.astream(
                new_state(user.session_id, user.role),
                config={"configurable": {
                    "thread_id": user.session_id,
                    "send": send,
                    "jwt_token": user.token,
                    "trace_id": trace_id,
                }},
                stream_mode="updates",
            ):
                for node_name, value in event.items():
                    if node_name == "__interrupt__":
                        for interrupt_obj in value:
                            payload = interrupt_obj.value
                            await ws_send(websocket, {
                                "type": "stream",
                                "text": payload.get("prompt", "") + "\n",
                            })
        except Exception as exc:
            log(trace_id=trace_id, session_id=user.session_id,
                node="orchestrator", event="error", extra={"error": str(exc)})
            import traceback; traceback.print_exc()
            await ws_send(websocket, {
                "type": "error", "code": "internal",
                "message": f"startup error: {exc}",
            })

    await ws_send(websocket, {"type": "done"})

    # ── Message loop ──────────────────────────────────────────────────────────
    while True:
        try:
            raw = await websocket.receive_json()
        except WebSocketDisconnect:
            log(trace_id=new_trace_id(), session_id=user.session_id,
                node="orchestrator", event="disconnect")
            break

        if raw.get("type") != "message" or not raw.get("text", "").strip():
            await ws_send(websocket, {
                "type": "error",
                "code": "internal",
                "message": "expected {type: 'message', text: '...'}",
            })
            continue

        user_text = raw["text"].strip()
        trace_id  = new_trace_id()

        try:
            await run_turn(
                user_text=user_text,
                user=user,
                send=send,
                trace_id=trace_id,
                is_first_turn=False,   # always False here — first turn ran above
            )
        except WebSocketDisconnect:
            log(trace_id=trace_id, session_id=user.session_id,
                node="orchestrator", event="disconnect")
            break
        except Exception as exc:
            log(trace_id=trace_id, session_id=user.session_id,
                node="orchestrator", event="error", extra={"error": str(exc)})
            await ws_send(websocket, {
                "type": "error", "code": "internal",
                "message": "unexpected server error",
            })

        await ws_send(websocket, {"type": "done"})


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "orchestrator-langgraph"}