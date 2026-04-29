"""Structured JSON trace logger.

Every node entry/exit and tool call emits one line to stdout:
    {"trace_id":"...","session_id":"...","node":"...","event":"enter","latency_ms":12,"ts":"..."}

trace_id is generated once per user turn and passed through all calls.
"""

import json
import time
import uuid
from datetime import datetime, timezone


def new_trace_id() -> str:
    """Generate a unique trace ID for a single user turn."""
    return uuid.uuid4().hex[:12]


def log(
    *,
    trace_id: str,
    session_id: str,
    node: str,
    event: str,           # "enter" | "exit" | "tool_call" | "tool_result" | "error"
    latency_ms: int = 0,
    extra: dict | None = None,
) -> None:
    """Emit one JSON trace line to stdout."""
    record = {
        "trace_id": trace_id,
        "session_id": session_id,
        "node": node,
        "event": event,
        "latency_ms": latency_ms,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        record.update(extra)
    print(json.dumps(record), flush=True)


class NodeTimer:
    """Context manager that logs enter/exit with latency for a node."""

    def __init__(self, *, trace_id: str, session_id: str, node: str):
        self.trace_id = trace_id
        self.session_id = session_id
        self.node = node
        self._start: float = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        log(
            trace_id=self.trace_id,
            session_id=self.session_id,
            node=self.node,
            event="enter",
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = int((time.perf_counter() - self._start) * 1000)
        log(
            trace_id=self.trace_id,
            session_id=self.session_id,
            node=self.node,
            event="exit" if exc_type is None else "error",
            latency_ms=elapsed_ms,
        )
        return False  # never suppress exceptions