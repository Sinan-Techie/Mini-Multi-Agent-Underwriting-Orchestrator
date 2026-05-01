"""Structured JSON trace logger.

Every node entry/exit and tool call emits one line to stdout AND is
appended to traces/{session_id}.jsonl for later retrieval via B5.

trace_id is generated once per user turn and propagates through all calls.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import TRACES_DIR


def new_trace_id() -> str:
    """Generate a unique trace ID for a single user turn."""
    return uuid.uuid4().hex[:12]


def _trace_path(session_id: str) -> Path:
    """Return the .jsonl file path for a session."""
    d = Path(TRACES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{session_id}.jsonl"


def log(
    *,
    trace_id: str,
    session_id: str,
    node: str,
    event: str,           
    latency_ms: int = 0,
    extra: dict | None = None,
) -> None:
    """Emit one JSON trace line to stdout and persist it to the session trace file."""
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

    line = json.dumps(record)

    # stdout (existing behaviour — keeps logs visible in terminal)
    print(line, flush=True)

    # Persist to traces/{session_id}.jsonl
    try:
        with _trace_path(session_id).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(json.dumps({"event": "trace_write_error", "error": str(exc)}), flush=True)


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
        return False