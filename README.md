# Take-Home Assignment — Mini Multi-Agent Underwriting Orchestrator

**Time budget:** You have 3 days to submit.

You can use any tool at your disposal. We want to see **production-quality code at small scale**. Clear decisions and honest documentation of tradeoffs matter more than a long feature list. Do not paste output you cannot explain.

---

## The problem

Build a FastAPI backend that exposes a **stateful, multi-agent insurance underwriting workflow** over WebSocket. A single orchestrator routes the user through three specialist agents in sequence. Tools are served from a **separate** HTTP service (a second process, not an in-process import). State must be **resumable** so a user who disconnects mid-flow can reconnect and continue.

```
                     ┌─────────────────────────────┐
   browser  ◄──ws──► │  FastAPI orchestrator       │ ──http──► tool service
                     │  (supervisor + 3 agents)    │           (separate process)
                     │  + local state store        │           (providers, pricing)
                     └─────────────────────────────┘
```

The orchestrator owns its own state store (single writer — no contention). The tool service is stateless.

**Use whichever framework you prefer** (LangGraph, plain Python state machine, your own — we don't care). What we score is **correctness, explainability, and clean separation of concerns**, not framework choice. Deterministic Python for routing and screening progression is **explicitly fine** — please don't reach for an LLM where a `match` statement does the job.

---

## Mandatory requirements (M1–M8)

### M1. WebSocket endpoint and protocol

Expose `/ws/chat`. The protocol below is exact — the test client expects these fields.

| Direction | Type | Shape |
|---|---|---|
| client → server | `auth` | `{"type":"auth","token":"<jwt>"}` |
| server → client | `auth_success` | `{"type":"auth_success","user_id":"...","role":"...","session_id":"..."}` |
| server → client | `auth_failed` | `{"type":"auth_failed","message":"..."}` (then close) |
| client → server | `message` | `{"type":"message","text":"..."}` |
| server → client | `node` | `{"type":"node","name":"eligibility_agent\|health_screening_agent\|quote_agent"}` |
| server → client | `stream` | `{"type":"stream","text":"<partial>"}` |
| server → client | `done` | `{"type":"done"}` (ends current turn, connection stays open) |
| server → client | `error` | `{"type":"error","code":"<code>","message":"..."}` |

**Error codes you must support:**
- `tool_forbidden` — the tool service returned 403
- `tool_unavailable` — the tool service was unreachable / 5xx
- `llm_failed` — the LLM provider errored
- `internal` — anything else

`error` events keep the connection open. Only `auth_failed` closes the socket.

### M2. JWT auth, 3 test users

`mint_tokens.py` is provided. Verify the JWT on every connection. Payload:

```json
{"sub":"agent-002","email":"agent@test.com","role":"agent","iat":...,"exp":...}
```

| user_id | role |
|---|---|
| `app-001` | `applicant` |
| `agent-002` | `agent` |
| `admin-003` | `admin` |

### M3. Three specialist agents under a supervisor

| Agent | Behaviour |
|---|---|
| `EligibilityAgent` | Collect `age` (18–75) and `region` (one of `UAE`, `KSA`, `IND`). Reject invalid input, ask again. |
| `HealthScreeningAgent` | Ask **3 fixed screening questions** (smoking, pre-existing conditions, weekly exercise hours). Store answers in state. The provided `docs/underwriting_sop.md` is for context — see B1 if you want to drive questions from it. |
| `QuoteAgent` | Call 3 mock pricing APIs in parallel (M5), pick the cheapest, stream the recommendation back. |

The supervisor decides the next agent based on state. **Deterministic routing is preferred** unless you have a reason to do otherwise.

State must be defined explicitly with a `TypedDict` or Pydantic model. Minimum fields: `current_node`, `eligibility`, `screening_answers`, `quote`, `last_user_msg`.

You do **not** need to support "go back" / state rewind. That is bonus (B3).

### M4. Separate tool service over HTTP

Stand up a **second process** (a FastAPI app on a different port) exposing two endpoints:

| Endpoint | Behaviour |
|---|---|
| `GET /tools/providers` | returns `["acme","globex","initech"]` |
| `GET /tools/pricing?provider=...&age=...&region=...` | `await asyncio.sleep(1)`, then return a deterministic mock price |

The orchestrator **must call these over HTTP** (e.g. `httpx.AsyncClient`), not import them. Treat the tool service as if it were across a network boundary.

The tool service is **stateless**. Session state lives only in the orchestrator (M7) — do not move it here.

You may shape the URLs differently if you prefer — these are illustrative.

### M5. Parallel pricing calls

`QuoteAgent` must call `/tools/pricing` once per provider, **in parallel**. Each call sleeps 1s. **Total elapsed time must be ~1s, not ~3s.**

After the calls return, stream the recommendation to the user (token-by-token from the LLM, OR chunk-by-chunk from a formatted string — see M6).

### M6. LLM provider, pluggable, with streaming

At least the QuoteAgent's recommendation must be **streamed**. The other agents may use plain string responses chunked in 2–3 pieces if you don't want to involve an LLM there.

Wrap the LLM behind a thin interface — e.g.:

```python
class LLMProvider(Protocol):
    async def stream(self, messages: list[dict]) -> AsyncIterator[str]: ...
```

Free-tier providers are fine (Groq, Gemini, OpenRouter, Ollama — see `.env.example`). State your choice in the submission README.

### M7. Resumable state

If the user disconnects and reconnects with the **same JWT** within 30 minutes, the conversation must resume from where it left off. State must survive a process restart of the orchestrator.

**Resumability semantics — read carefully:**
- **Persist after every completed user turn** (i.e. after `done` is sent), not mid-stream.
- **Reconnect resumes at the last durable node boundary**, not in the middle of a streamed response.
- `session_id` is derived deterministically from JWT `sub` (e.g. `sha256(sub)[:16]`).
- On reconnect, the server sends `auth_success`, then a `node` event with the resume node, then waits for the next user message.

Use SQLite, a JSON file, or Redis — your choice. Justify it in 1–2 lines.

### M8. RBAC at the tool layer

| Role | Allowed tools |
|---|---|
| `applicant` | `providers` |
| `agent` | `providers`, `pricing` |
| `admin` | `providers`, `pricing`, `traces` (B5) |

The **tool service** enforces this — not the orchestrator. Forward the JWT in an `Authorization` header on every tool call. On 403, the orchestrator emits `{"type":"error","code":"tool_forbidden","message":"..."}`. Connection stays open.

The `applicant` role is intentionally blocked at the quote step. This is the failure path we will test.

---

## Bonus (B1–B5)

Pick one or two if you have time. **Skipping all of them is fine** — full marks on M1–M8 + a good README beats half-finished bonus work.

| ID | Item |
|---|---|
| B1 | **Drive screening questions from the SOP via small RAG.** Chunk `docs/underwriting_sop.md`, embed locally with `sentence-transformers`, retrieve top-k per turn. |
| B2 | **PII redaction** before sending screening answers to the LLM (email, 7+ digit phone, `Mr./Mrs./Ms. <Name>`). |
| B3 | **State rewind.** If the user types "I want to change my age", the supervisor routes back to `EligibilityAgent` cleanly without corrupting state. |
| B4 | **Hot-reloadable prompts** in `prompts/*.yaml`, picked up on next turn without restart. |
| B5 | **`GET /traces?session_id=...`** admin-only endpoint returning the trace log for a session. |

---

## Observability (mandatory baseline, lightweight)

For every node entry/exit and every tool call, emit one JSON log line to stdout:

```json
{"trace_id":"...","session_id":"...","node":"...","event":"enter|exit|tool_call","latency_ms":12,"ts":"..."}
```

`trace_id` is generated per **user turn** and propagates through every node and tool call in that turn. No Loki/Grafana setup required.

---

## Test client

`test_client.html` speaks the M1 protocol including `node` and `error` events. Open it in a browser, paste a JWT, click Connect.

**To run:**
1. Copy `.env.example` → `.env`, set `JWT_SECRET`.
2. `python3 mint_tokens.py` to print three JWTs.
3. Start tool service: `uvicorn tools.main:app --port 8001`.
4. Start orchestrator: `uvicorn app.main:app --port 8000`.
5. Open `test_client.html`, paste a JWT, connect, chat.

**To verify resumability:** disconnect during the screening, reload, reconnect with the same JWT, confirm the `node` event tells you where you resumed.

**To verify RBAC:** connect as `applicant`, walk through to the quote step, confirm you get an `error` with `code: "tool_forbidden"` and the connection stays open.

---

## What to submit

Zip the project, or push to GitHub. Include a `README_SUBMISSION.md` with:

1. How to run (tool service + orchestrator).
2. **State machine diagram** — ASCII or hand-drawn is fine.
3. **Resumability** — what's persisted, where, when, how recovery works on reconnect.
4. **RBAC** — show the exact place the 403 is raised in the tool service.
5. **Parallel** — paste the `asyncio.gather` snippet from QuoteAgent.
6. **Streaming** — how `node`, `stream`, `done`, `error` are multiplexed on one WS.
7. **One thing you would improve** given more time.
8. **One tradeoff you made** and why.

State which LLM provider you chose.

---

## Ground rules

- If a requirement is unclear, make a reasonable assumption and document it. Judgment beats pedantic interpretation.
- Show your commits. We want to see how you iterate.
- If you got stuck on something, write 2–3 lines about what you tried and what you'd do next. An honest gap beats a silent one.
- **Don't spend more than ~6 hours.** If you're over budget, stop and write up what's left for "given more time".

Good luck.
