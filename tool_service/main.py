"""Tool service — stateless FastAPI app on port 8001.

Endpoints:
  GET /health                                           — health probe (no auth)
  GET /tools/providers                                  — list providers
  GET /tools/pricing?provider=&age=&region=             — mock price (agent + admin)
  GET /traces?session_id=...                            — session trace log (admin only)
        optional filters: node, event, trace_id, since, until

RBAC is enforced HERE via JWT forwarded in Authorization: Bearer <token>.

Each /tools/pricing call sleeps 1 s — QuoteAgent calls all 3 in parallel (~1 s total).
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Security, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .rbac import require_permission
from .pricing import calculate_price, PROVIDERS
from config import TRACES_DIR

app = FastAPI(title="Underwriting Tool Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "tool_service"}


# ---------------------------------------------------------------------------
# GET /tools/providers
# Allowed: applicant, agent, admin
# ---------------------------------------------------------------------------

@app.get("/tools/providers")
async def get_providers(
    _payload: dict = Security(require_permission("providers")),
):
    """Return the list of available insurance providers."""
    return {"providers": PROVIDERS}


# ---------------------------------------------------------------------------
# GET /tools/pricing
# Allowed: agent, admin   (applicant → 403)
# ---------------------------------------------------------------------------

@app.get("/tools/pricing")
async def get_pricing(
    provider: str  = Query(..., description="One of: acme, globex, initech"),
    age: int       = Query(..., ge=18, le=75, description="Applicant age"),
    region: str    = Query(..., description="One of: UAE, KSA, IND"),
    _payload: dict = Security(require_permission("pricing")),
):
    """
    Return a deterministic annual premium for the given provider/age/region.
    Simulates real API latency with a 1-second sleep.
    QuoteAgent calls this endpoint 3x in parallel so total is ~1 s not ~3 s.
    """
    await asyncio.sleep(1)

    try:
        price = calculate_price(provider=provider, age=age, region=region)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "provider": provider,
        "age": age,
        "region": region,
        "annual_premium_usd": price,
        "currency": "USD",
    }


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str) -> datetime:
    """Parse an ISO timestamp string into a timezone-aware datetime."""
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_iso(value: str, param_name: str) -> datetime:
    """Validate an ISO timestamp query param. Raises 422 on bad format."""
    try:
        return _parse_ts(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{param_name}' must be a valid ISO 8601 timestamp "
                f"(e.g. 2026-04-30T18:00:00+00:00). Got: {value!r}"
            ),
        )


def _build_summary(records: list[dict]) -> dict:
    """
    Compute an at-a-glance summary over a list of trace records.
    Useful for an admin to assess session health without reading every line.
    """
    nodes_seen: set[str] = set()
    trace_ids_seen: set[str] = set()
    error_count = 0
    tool_calls = 0
    latency_by_node: dict[str, list[int]] = {}

    for r in records:
        node = r.get("node", "")
        event = r.get("event", "")
        tid = r.get("trace_id", "")
        latency = r.get("latency_ms", 0)

        if node:
            nodes_seen.add(node)
        if tid:
            trace_ids_seen.add(tid)
        if event == "error":
            error_count += 1
        if event == "tool_call":
            tool_calls += 1
        if event == "exit" and latency:
            latency_by_node.setdefault(node, []).append(latency)

    avg_latency = {
        node: round(sum(v) / len(v), 1)
        for node, v in latency_by_node.items()
    }

    return {
        "unique_turns": len(trace_ids_seen),
        "nodes_visited": sorted(nodes_seen),
        "error_count": error_count,
        "tool_calls": tool_calls,
        "avg_latency_ms_by_node": avg_latency,
    }


# ---------------------------------------------------------------------------
# GET /traces
# Allowed: admin only
# ---------------------------------------------------------------------------

@app.get("/traces")
async def get_traces(
    session_id: str            = Query(...,  description="Session ID to retrieve traces for"),
    node:       Optional[str]  = Query(None, description="Filter by node name (e.g. quote_agent)"),
    event:      Optional[str]  = Query(None, description="Filter by event type (enter, exit, tool_call, tool_result, error)"),
    trace_id:   Optional[str]  = Query(None, description="Filter to a single user turn by trace_id"),
    since:      Optional[str]  = Query(None, description="ISO 8601 timestamp — return events at or after this time"),
    until:      Optional[str]  = Query(None, description="ISO 8601 timestamp — return events at or before this time"),
    _payload:   dict           = Security(require_permission("traces")),
):
    """
    Return trace events for a session with optional filtering.

    All filter params are optional and combinable:
      - node       : exact match on the 'node' field
      - event      : exact match on the 'event' field
      - trace_id   : isolate one user turn end-to-end
      - since/until: time-bound the results (ISO 8601)

    Response includes:
      - summary         : at-a-glance stats over the FULL session (unfiltered)
      - traces          : the filtered events
      - total_count     : total events in the session file
      - filtered_count  : events returned after applying filters
      - filters_applied : echo back which filters were active
    """
    trace_file = Path(TRACES_DIR) / f"{session_id}.jsonl"

    if not trace_file.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No traces found for session_id '{session_id}'. "
                "The session may not exist or may not have produced any events yet."
            ),
        )

    # Parse timestamp filters early — fail fast on bad input before reading file
    since_dt = _validate_iso(since, "since") if since else None
    until_dt = _validate_iso(until, "until") if until else None

    if since_dt and until_dt and since_dt > until_dt:
        raise HTTPException(
            status_code=422,
            detail="'since' must be earlier than 'until'.",
        )

    # Read all records from the JSONL file
    all_records: list[dict] = []
    with trace_file.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                all_records.append(json.loads(raw))
            except json.JSONDecodeError:
                # Surface malformed lines without crashing the endpoint
                all_records.append({
                    "parse_error": True,
                    "line": lineno,
                    "raw": raw[:200],
                })

    # Summary is always computed over the full unfiltered dataset
    summary = _build_summary(all_records)

    # Apply filters — all are optional and combinable
    filtered = all_records

    if node:
        filtered = [r for r in filtered if r.get("node") == node]

    if event:
        filtered = [r for r in filtered if r.get("event") == event]

    if trace_id:
        filtered = [r for r in filtered if r.get("trace_id") == trace_id]

    if since_dt:
        filtered = [
            r for r in filtered
            if "ts" in r and _parse_ts(r["ts"]) >= since_dt
        ]

    if until_dt:
        filtered = [
            r for r in filtered
            if "ts" in r and _parse_ts(r["ts"]) <= until_dt
        ]

    return {
        "session_id": session_id,
        "total_count": len(all_records),
        "filtered_count": len(filtered),
        "filters_applied": {
            k: v for k, v in {
                "node": node,
                "event": event,
                "trace_id": trace_id,
                "since": since,
                "until": until,
            }.items() if v is not None
        },
        "summary": summary,
        "traces": filtered,
    }