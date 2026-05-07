"""
nodes/screening.py

Replaces agents/screening.py.

The original used:
    state["awaiting_answer"] = True / False
    state["current_question"] = "tobacco" | "preexisting" | "exercise"
    early return state  →  WS layer re-sends the question on resume

The LangGraph version uses interrupt() instead:
    interrupt({"node": "...", "prompt": QUESTIONS[q]})
    →  graph pauses, WS sends prompt + done
    →  on next user message, graph resumes with the answer as the return value

The queue / step counter logic is IDENTICAL to the original —
just the "pause and wait" mechanism is different.
"""

from langgraph.types import interrupt

from ..state import UnderwritingState, ScreeningAnswers


QUESTIONS = {
    "tobacco":    "Do you currently use any form of tobacco? (yes/no)",
    "preexisting": "Have you had diabetes, heart disease, cancer, or respiratory illness? (yes/no)",
    "exercise":   "How many hours per week do you exercise?",
}

QUESTION_ORDER = ["tobacco", "preexisting", "exercise"]


def _build_initial_queue() -> list[str]:
    return list(QUESTION_ORDER)


async def screening_node(state: UnderwritingState) -> dict:
    """
    Ask the screening questions one at a time using interrupt().

    State shape on entry:
        screening_queue: [] on first call, populated thereafter
        screening_step:  index into the queue

    The node runs a tight loop:
        ask → interrupt → validate → store → advance step → ask next

    Because each interrupt() pauses and resumes execution inside THIS
    function call, the loop naturally advances through all questions
    in a single conceptual "node execution" across multiple WS turns.
    """

    # ── Initialise queue on first entry ──────────────────────────────────────
    queue = state.get("screening_queue") or _build_initial_queue()
    step  = state.get("screening_step", 0)
    answers: ScreeningAnswers = dict(state.get("screening_answers") or {})

    # ── Work through remaining questions ─────────────────────────────────────
    while step < len(queue):
        current = queue[step]

        # Ask the question — graph pauses here until user replies.
        user_text: str = interrupt(
            {
                "node": "health_screening_agent",
                "prompt": QUESTIONS[current],
            }
        )

        text = user_text.strip().lower()

        # ── Validate and store ────────────────────────────────────────────────
        if current == "tobacco":
            if text not in {"yes", "no"}:
                # Re-ask on bad input
                user_text = interrupt(
                    {
                        "node": "health_screening_agent",
                        "prompt": "Please answer yes or no.\n" + QUESTIONS[current],
                    }
                )
                text = user_text.strip().lower()
            answers["tobacco_current"] = (text == "yes")

        elif current == "preexisting":
            if text not in {"yes", "no"}:
                user_text = interrupt(
                    {
                        "node": "health_screening_agent",
                        "prompt": "Please answer yes or no.\n" + QUESTIONS[current],
                    }
                )
                text = user_text.strip().lower()
            answers["preexisting_conditions"] = (text == "yes")

        elif current == "exercise":
            try:
                answers["exercise_hours_per_week"] = float(text)
            except ValueError:
                user_text = interrupt(
                    {
                        "node": "health_screening_agent",
                        "prompt": (
                            "Please enter a number (e.g. 0, 1.5, 3).\n"
                            + QUESTIONS[current]
                        ),
                    }
                )
                answers["exercise_hours_per_week"] = float(user_text.strip())

        step += 1

        # Yield partial state after each question so the checkpointer
        # can save progress — handled automatically by LangGraph between
        # interrupt/resume cycles.

    # ── All questions answered — return final state ───────────────────────────
    return {
        "screening_answers": ScreeningAnswers(**answers),
        "screening_queue":   queue,
        "screening_step":    step,
        "last_user_msg":     user_text if "user_text" in dir() else "",
    }