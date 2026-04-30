from typing import Callable, Awaitable
from ..state import SessionState
from ..auth import AuthUser


# ---------------------------------------------------------------------------
# QUESTIONS (SOP aligned)
# ---------------------------------------------------------------------------

QUESTIONS = {
    "general_health": "On a scale of 1 (poor) to 5 (excellent), how would you rate your general health?",
    "general_health_followup": "What is the primary factor affecting your health?",

    "tobacco": "Do you currently use any form of tobacco? (yes/no)",
    "tobacco_yes_followup": "How many years have you used tobacco and how often per week?",
    "tobacco_no_followup": "Have you used tobacco regularly in the past 12 months? (yes/no)",

    "alcohol": "How many alcoholic drinks do you consume per week? (number or 'skip')",

    "preexisting": "Have you had diabetes, heart disease, cancer, or respiratory illness in last 5 years? (yes/no)",
    "preexisting_followup": "Please specify condition and year of diagnosis.",

    "family_history": "Any immediate family member diagnosed with serious heart condition or cancer before 60? (yes/no)",

    "exercise": "How many hours per week do you exercise?",

    "travel": "Do you travel internationally more than 60 days/year? (yes/no)",
}


# ---------------------------------------------------------------------------
# QUEUE BUILDER
# ---------------------------------------------------------------------------

def build_initial_queue(region: str) -> list[str]:
    queue = [
        "general_health",
        "tobacco",
        "alcohol",
        "family_history",
        "exercise",
    ]

    if region in {"UAE", "KSA"}:
        queue.append("travel")

    return queue


# ---------------------------------------------------------------------------
# AGENT (FSM-BASED)
# ---------------------------------------------------------------------------

async def handle(
    *,
    user_text: str,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
):
    answers = state["screening_answers"]

    # -----------------------------------------------------------------------
    # INIT
    # -----------------------------------------------------------------------
    if not state["screening_queue"]:
        region = state["eligibility"]["region"]
        state["screening_queue"] = build_initial_queue(region)
        state["screening_step"] = 0
        state["awaiting_answer"] = False
        state["current_question"] = None
        state["followup_key"] = None

    queue = state["screening_queue"]
    step = state["screening_step"]

    # -----------------------------------------------------------------------
    # COMPLETE
    # -----------------------------------------------------------------------
    if step >= len(queue):
        state["current_node"] = "quote_agent"
        await send({
            "type": "stream",
            "text": "Screening complete. Moving to quote.\n",
        })
        return state

    # -----------------------------------------------------------------------
    # ASK QUESTION (if not awaiting)
    # -----------------------------------------------------------------------
    if not state.get("awaiting_answer"):
        current = queue[step]

        await send({
            "type": "stream",
            "text": QUESTIONS[current] + "\n",
        })

        state["current_question"] = current
        state["awaiting_answer"] = True
        return state

    # -----------------------------------------------------------------------
    # IGNORE EMPTY INPUT
    # -----------------------------------------------------------------------
    if not user_text:
        return state

    text = user_text.strip().lower()
    current = state["current_question"]

    # -----------------------------------------------------------------------
    # HANDLE FOLLOWUP FIRST (BLOCKING)
    # -----------------------------------------------------------------------
    if state.get("followup_key"):
        key = state["followup_key"]

        if key == "general_health_followup":
            answers["health_primary_factor"] = user_text

        elif key == "tobacco_yes_followup":
            answers["tobacco_years"] = user_text

        elif key == "tobacco_no_followup":
            if text not in {"yes", "no"}:
                await send({"type": "stream", "text": "Answer yes or no.\n"})
                return state
            answers["tobacco_past_12m"] = (text == "yes")

        elif key == "preexisting_followup":
            answers["preexisting_detail"] = user_text

        # clear followup and advance
        state["followup_key"] = None
        state["awaiting_answer"] = False
        state["screening_step"] += 1

        return await handle(
            user_text="",
            state=state,
            user=user,
            send=send
        )

    # -----------------------------------------------------------------------
    # MAIN QUESTION PROCESSING
    # -----------------------------------------------------------------------

    # ---------- GENERAL HEALTH ----------
    if current == "general_health":
        try:
            score = int(text)
        except ValueError:
            await send({"type": "stream", "text": "Enter a number between 1 and 5.\n"})
            return state

        if not (1 <= score <= 5):
            await send({"type": "stream", "text": "Must be between 1 and 5.\n"})
            return state

        answers["general_health_score"] = score

        if score <= 2:
            state["followup_key"] = "general_health_followup"
            await send({"type": "stream", "text": QUESTIONS["general_health_followup"] + "\n"})
            return state

    # ---------- TOBACCO ----------
    elif current == "tobacco":
        if text not in {"yes", "no"}:
            await send({"type": "stream", "text": "Answer yes or no.\n"})
            return state

        answers["tobacco_current"] = (text == "yes")

        if text == "yes":
            state["followup_key"] = "tobacco_yes_followup"
            await send({"type": "stream", "text": QUESTIONS["tobacco_yes_followup"] + "\n"})
            return state
        else:
            state["followup_key"] = "tobacco_no_followup"
            await send({"type": "stream", "text": QUESTIONS["tobacco_no_followup"] + "\n"})
            return state

    # ---------- ALCOHOL ----------
    elif current == "alcohol":
        if text == "skip":
            answers["alcohol_drinks_per_week"] = None
        else:
            try:
                answers["alcohol_drinks_per_week"] = int(text)
            except ValueError:
                await send({"type": "stream", "text": "Enter a number or 'skip'.\n"})
                return state

    # ---------- FAMILY HISTORY ----------
    elif current == "family_history":
        if text not in {"yes", "no"}:
            await send({"type": "stream", "text": "Answer yes or no.\n"})
            return state

        answers["family_history"] = (text == "yes")

        # CONDITIONAL INSERT (SAFE)
        if (
            answers.get("general_health_score", 5) < 4
            or answers.get("tobacco_current")
        ):
            if "preexisting" not in queue:
                queue.insert(step + 1, "preexisting")

    # ---------- PREEXISTING ----------
    elif current == "preexisting":
        if text not in {"yes", "no"}:
            await send({"type": "stream", "text": "Answer yes or no.\n"})
            return state

        answers["preexisting_conditions"] = text

        if text == "yes":
            state["followup_key"] = "preexisting_followup"
            await send({"type": "stream", "text": QUESTIONS["preexisting_followup"] + "\n"})
            return state

    # ---------- EXERCISE ----------
    elif current == "exercise":
        try:
            answers["exercise_hours_per_week"] = float(text)
        except ValueError:
            await send({"type": "stream", "text": "Enter a number.\n"})
            return state

    # ---------- TRAVEL ----------
    elif current == "travel":
        if text not in {"yes", "no"}:
            await send({"type": "stream", "text": "Answer yes or no.\n"})
            return state

        answers["travel_international_60d"] = (text == "yes")

    # -----------------------------------------------------------------------
    # ADVANCE STEP
    # -----------------------------------------------------------------------
    state["awaiting_answer"] = False
    state["screening_step"] += 1

    return await handle(
        user_text="",
        state=state,
        user=user,
        send=send
    )