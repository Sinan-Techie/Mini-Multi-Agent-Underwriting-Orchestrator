"""Session state model and SQLite-backed persistence.

Design decisions:
  - Single writer (orchestrator only) — no contention, SQLite is sufficient.
  - Persisted only after 'done' is sent — not on mid-stream.
  - Resumable within SESSION_TTL_SECONDS (30 min).
  - State is serialised as JSON in a single TEXT column for simplicity.

Schema:
    sessions(session_id TEXT PK, state TEXT, updated_at REAL)
"""

import json
import sqlite3
import time
from typing import TypedDict

from config import STATE_SQLITE_PATH, SESSION_TTL_SECONDS




class EligibilityData(TypedDict, total=False):
    age: int
    region: str


class ScreeningAnswers(TypedDict, total=False):
    general_health_score: int
    health_primary_factor: str
    tobacco_current: bool
    tobacco_years: str
    tobacco_past_12m: bool
    alcohol_drinks_per_week: int | None
    preexisting_asked: bool
    preexisting_conditions: str
    preexisting_detail: str
    family_history: bool
    family_history_detail: str
    exercise_hours_per_week: float
    travel_international_60d: bool | None


class QuoteData(TypedDict, total=False):
    provider: str
    price: float
    all_prices: dict


class SessionState(TypedDict):
    session_id: str
    role: str
    current_node: str
    eligibility: EligibilityData
    screening_answers: ScreeningAnswers
    screening_step: int
    screening_queue: list[str]
    quote: QuoteData
    quote_streamed: bool
    quote_recommendation: str
    last_user_msg: str
    updated_at: float


def new_state(session_id: str, role: str) -> SessionState:
    """Create a fresh session state starting at the eligibility node."""
    return SessionState(
        session_id=session_id,
        role=role,
        current_node="eligibility_agent",
        eligibility=EligibilityData(),
        screening_answers=ScreeningAnswers(),
        screening_step=0,
        screening_queue=[],
        quote=QuoteData(),
        quote_streamed=False,
        quote_recommendation="",
        last_user_msg="",
        updated_at=time.time(),
    )


# SQLite helpers

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(STATE_SQLITE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            state       TEXT NOT NULL,
            updated_at  REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def save_state(session_id: str, state: SessionState) -> None:
    """Upsert session state. Called only after 'done' is sent."""
    state["updated_at"] = time.time()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                state      = excluded.state,
                updated_at = excluded.updated_at
            """,
            (session_id, json.dumps(state), state["updated_at"]),
        )


def load_state(session_id: str) -> SessionState | None:
    """
    Load session state if it exists and is within TTL.
    Returns None for new or expired sessions.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT state, updated_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()

    if row is None:
        return None

    state_json, updated_at = row
    age_seconds = time.time() - updated_at

    if age_seconds > SESSION_TTL_SECONDS:
        # Expired — treat as new session
        delete_state(session_id)
        return None

    return json.loads(state_json)


def delete_state(session_id: str) -> None:
    """Remove a session (expired or completed)."""
    with _get_conn() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE session_id = ?",
            (session_id,),
        )