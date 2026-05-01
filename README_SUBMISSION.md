# README_SUBMISSION.md

## 1. How to run

```bash
git clone https://github.com/Sinan-Techie/Mini-Multi-Agent-Underwriting-Orchestrator.git
cd Mini-Multi-Agent-Underwriting-Orchestrator

python -m venv .venv
.venv\Scripts\activate  # MAC: source .venv/bin/activate

# 1. Install requirements
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Set JWT_SECRET and GROQ_API_KEY in .env

# 3. Mint test tokens
python mint_tokens.py

# 4. Start tool service (port 8001)
uvicorn tool_service.main:app --port 8001

# 5. Start orchestrator (port 8000)
uvicorn orchestrator.main:app --port 8000

# 6. Open test_client.html in a browser, paste a JWT, connect
```

---

## 2. State machine diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         WS CONNECT                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    JWT auth handshake
                           │
               ┌───────────┴───────────┐
               │ invalid/expired token │
               └───────────┬───────────┘
                           │ auth_failed + close
                           ▼
                        [CLOSED]

               ┌───────────┴───────────┐  (valid token)
               │    load_state()       │
               └───────────┬───────────┘
                           │
              ┌────────────┴─────────────┐
              │                          │
         new session                 resume (within 30min)
              │                          │
              ▼                          ▼
     current_node =            send auth_success
     eligibility_agent         send node(current_node)
              │                send resume prompt
              │                          │
              └────────────┬─────────────┘
                           │
                    MESSAGE LOOP
                           │
          ┌────────────────▼────────────────┐
          │         receive message          │
          │   {type: message, text: ...}     │
          └────────────────┬────────────────┘
                           │
                    router.handle_turn()
                           │
          ┌────────────────▼────────────────┐
          │      match current_node         │
          └────────────────┬────────────────┘
                           │
     ┌─────────────────────┼──────────────────────┐
     │                     │                      │
     ▼                     ▼                      ▼
eligibility_agent   health_screening_agent    quote_agent


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ELIGIBILITY AGENT   (current_node = "eligibility_agent")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

     user_text received
           │
     _parse_input(text)  →  expects "30 IND"
           │
     ┌─────┴──────┐
   invalid       valid
     │              │
     │         age 18–75?   region in {UAE,KSA,IND}?
     │              │
     │         ┌────┴────┐
     │       fail       pass
     │         │          │
     ▼         ▼          ▼
  re-prompt  re-prompt  state["eligibility"] = {age, region}
  (stay)     (stay)     state["current_node"] = "health_screening_agent"
                        │
                        │  [chains directly — same turn]
                        ▼
               screening_agent.handle(user_text="")


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HEALTH SCREENING AGENT  (current_node = "health_screening_agent")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  screening_queue = ["tobacco", "preexisting", "exercise"]
  screening_step  = 0

  Each turn:

  step >= len(queue)?
       │
  ┌────┴────┐
 yes        no
  │          │
  │    awaiting_answer?
  │          │
  │     ┌────┴────┐
  │    yes        no
  │     │          │
  │     │       ask QUESTIONS[queue[step]]
  │     │       awaiting_answer = True
  │     │       return (wait for next user turn)
  │     │
  │   validate answer
  │     │
  │   ┌─┴──────────┐
  │  fail          pass
  │   │              │
  │  re-prompt     store answer
  │  (stay)        screening_step += 1
  │                recurse handle(user_text="")
  │                  → asks next question or
  │                    falls into step >= len(queue)
  │
  ▼
  state["current_node"] = "quote_agent"
  save_state()                              ← only explicit mid-flow save
  │
  │  [chains directly — same turn]
  ▼
  quote_agent.handle()


  Question validation rules:
    tobacco     → must be "yes" | "no"   else re-prompt
    preexisting → must be "yes" | "no"   else re-prompt
    exercise    → must parse as float()  else re-prompt


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUOTE AGENT  (current_node = "quote_agent")
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Three resume paths checked in order:

  1. quote set AND quote_streamed = True
       → replay saved recommendation text
       → no API or LLM call

  2. quote set AND quote_streamed = False
       → prices already fetched, stream interrupted
       → skip API calls, re-run LLM stream

  3. quote empty (fresh)
       → get_providers()           HTTP → tool service
       → get_all_pricing_parallel() asyncio.gather x3 (~1s total)
            acme ──┐
            globex ─┼── parallel ──► pick cheapest
            initech┘
       → state["quote"] = {provider, price, all_prices}

  Then in all non-replay paths:
       → llm.stream(messages)      Groq streaming
       → accumulate tokens → state["quote_recommendation"]
       → state["quote_streamed"] = True


  Error paths:
    ToolForbiddenError  → error{tool_forbidden}   connection stays open
    ToolUnavailableError→ error{tool_unavailable}  connection stays open
    LLM exception       → error{llm_failed}
                          fallback plain-text recommendation


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STATE PERSISTENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  save_state() called:
    1. After every "done" in message loop       (main.py)
    2. Before chaining to quote_agent           (screening.py)

  load_state() called:
    On every WS connect — returns None if expired (>30min) or new

  SQLite: sessions(session_id PK, state JSON, updated_at REAL)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RBAC  (enforced in tool service, not orchestrator)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  applicant → providers only         (blocked at quote step → 403)
  agent     → providers + pricing
  admin     → providers + pricing + traces
```

---

## 3. Resumability

**What is persisted:** The full `SessionState` TypedDict serialised as JSON in a
single SQLite row — `sessions(session_id PK, state TEXT, updated_at REAL)`.

**When it is persisted:** After every completed user turn (after `done` is sent) in
the main message loop in `main.py`. One additional explicit save happens in
`screening.py` before chaining to `quote_agent`, to ensure the completed screening
state survives a disconnect that occurs during the quote fetch.

**Recovery on reconnect:** `load_state()` checks the `updated_at` timestamp against
the 30-minute TTL. On a valid resume the server sends `auth_success`, then a `node`
event with the current node, then re-sends the last unanswered question (or replays
the quote if already generated). The client resumes exactly where it left off.

**Why SQLite:** The orchestrator is the single writer — there is no concurrent access.
SQLite is zero-setup, survives process restarts (the state file is on disk), and
`INSERT OR REPLACE` gives atomic upserts.

---

## 4. RBAC

Enforced exclusively in the **tool service** (`tool_service/rbac.py`), not in the
orchestrator. The orchestrator forwards the original JWT in an `Authorization: Bearer`
header on every tool call.

Permission table — single source of truth:

```python
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "applicant": {"providers"},
    "agent":     {"providers", "pricing"},
    "admin":     {"providers", "pricing", "traces"},
}
```

The 403 is raised here, inside `require_permission()`:

```python
if tool not in allowed:
    raise HTTPException(
        status_code=403,
        detail=(
            f"role '{role}' is not permitted to access tool '{tool}'. "
            f"Allowed tools for this role: {sorted(allowed)}"
        ),
    )
```

The orchestrator's `tool_client.py` catches HTTP 403 and raises `ToolForbiddenError`,
which the message loop converts to `{"type": "error", "code": "tool_forbidden"}`.
The connection stays open.

**Test path:** connect as `applicant`, complete eligibility and screening — on the
quote step the tool service returns 403 and the client sees a `tool_forbidden` error
while remaining connected.

---

## 5. Parallel pricing calls

From `tool_client.py`:

```python
tasks = [
    get_pricing(provider=p, age=age, region=region,
                jwt_token=jwt_token, trace_id=trace_id, session_id=session_id)
    for p in providers
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

Each `get_pricing` call hits `/tools/pricing` which sleeps `await asyncio.sleep(1)`.
All three fire simultaneously via `asyncio.gather` so total elapsed is ~1s, not ~3s.
`return_exceptions=True` ensures one provider failure does not cancel the others —
partial results are used and failures are logged. If all three fail, the last exception
is re-raised.

---

## 6. Streaming — how node, stream, done, error are multiplexed on one WebSocket

All events share a single WebSocket connection and are multiplexed purely by the
`type` field.

The sequence for a normal turn:

```
client → {type: message, text: "..."}
server → {type: node,   name: "quote_agent"}       # which agent owns this turn
server → {type: stream, text: "Based on your..."}  # token 1
server → {type: stream, text: " profile..."}       # token 2  (N more)
server → {type: done}                              # turn complete
```

`make_send()` in `main.py` wraps `websocket.send_json()` and normalises all
disconnect exceptions to `WebSocketDisconnect`, so every agent uses the same `send()`
callable without its own socket error handling. The message loop is the single place
that catches disconnects and exits cleanly.

Error events are non-terminal — they are injected into the same stream and followed
by `done`. Only `auth_failed` closes the socket.

---

## 7. One thing I would improve given more time

**Given more time, I would extend the screening phase with dynamic questions driven by the SOP document.**

The current implementation uses 3 hardcoded questions as specified in the README. If I had more time I would replace these with questions extracted from `docs/underwriting_sop.md` at runtime — not via RAG (B1), but by feeding the SOP document directly to the LLM and having it generate contextually relevant follow-up questions based on the applicant's profile (age, region) and prior answers. Validation would also be LLM-driven rather than the current hard `yes/no` and `float()` checks.

This was a conscious sequencing decision — I chose B5 early because the trace infrastructure needed to be modular from the start to support it cleanly, and I kept screening grounded to the 3 questions explicitly listed in the README rather than shipping a fuller version that went beyond the spec. The router-based agent chaining design would naturally support this extension since each screening turn would simply return state and wait for the next message, with the LLM deciding the next question dynamically.

---

## 8. One tradeoff I made and why

**Agent chaining vs. supervisor-only routing.**

The README recommends deterministic supervisor routing where the router decides the
next agent after each turn. An earlier version of this codebase (`main-feature/quote-agent-integration`)
followed that model exactly — each agent completed its work, returned a confirmation
message, and the user typed something like "ok" to advance. The supervisor read
`current_node` from state and dispatched accordingly.

That approach is architecturally cleaner but creates a jarring UX: after valid
eligibility input the user is told "eligibility done" and then has to send another
message just to start screening. There is no meaningful decision to make at that
boundary — the transition is always automatic.

The current implementation chains agents directly within the same turn at natural
boundaries (eligibility success → start screening; all screening questions answered →
fetch quote). The user experience is seamless and the state machine is still fully
explicit — `current_node` is always updated before any chain call, so a disconnect
at any point resumes correctly.

The tradeoff is that `router.py` only handles the first dispatch of each phase — mid-phase
transitions happen inside the agent itself. Acceptable at this scale; worth revisiting
if the workflow grows.

---

## Development process — git history

This submission was built incrementally with one feature branch per requirement,
each merged to `main` via pull request after end-to-end testing. The branch history
reflects the evolution of the design:

| PR | Branch                                         | What changed                                                                                                                                                   |
| -- | ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #1 | `main-feature/tool_server_integration`       | Tool service with RBAC, JWT forwarding                                                                                                                         |
| #2 | `main-feature/eligibility-agent-integration` | Eligibility agent with age/region validation                                                                                                                   |
| #3 | `main-feature/screening-agent-integration`   | Deterministic screening FSM, full question set                                                                                                                 |
| #4 | `main-feature/quote-agent-integration`       | Quote agent, parallel pricing, Groq streaming. At this stage agents did**not** chain — each completed and the user advanced manually via the supervisor |
| #5 | `main-feature/grounding_screeing-3-question` | Grounded screening to exactly 3 questions per README spec (tobacco, preexisting, exercise)                                                                     |
| #6 | `main-feature/edge-cases`                    | Reconnect edge cases, state consistency fixes                                                                                                                  |
| #7 | `main-feature/node-boundry-case`             | Fixed node boundary resume during mid-quote disconnect                                                                                                         |
| #8 | `bonus5-feature/trace-route-for-admin`       | B5:`/traces` endpoint with filtering, admin-only RBAC                                                                                                        |
| #9 | `bonus5-feature/finishing-touches`           | End-to-end test pass, error handling cleanup                                                                                                                   |

To see the supervisor-only routing design (no agent chaining) and To see the expanded screening question set
before it was grounded to 3, check out `main-feature/quote-agent-integration`.

## LLM Provider

**Groq** — `llama-3.3-70b-versatile` via the official `groq` Python SDK.
Chosen for its free tier, low latency, and native async streaming support which maps
directly onto the `stream` event protocol.
