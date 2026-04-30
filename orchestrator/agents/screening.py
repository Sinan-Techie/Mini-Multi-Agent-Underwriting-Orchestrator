from typing import Callable, Awaitable
from ..state import SessionState
from ..auth import AuthUser



# QUESTIONS (SOP aligned)


QUESTIONS = {
    "tobacco": "Do you currently use any form of tobacco? (yes/no)",
    "preexisting": "Have you had diabetes, heart disease, cancer, or respiratory illness in last 5 years? (yes/no)",
    "exercise": "How many hours per week do you exercise?",
}


# QUEUE BUILDER

def build_initial_queue(region: str) -> list[str]:
    return [
        "tobacco",
        "preexisting",
        "exercise",
    ]

# AGENT (FSM-BASED)

async def handle(
    *,
    user_text: str,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
):
    answers = state["screening_answers"]


    # INIT

    if not state["screening_queue"]:
        region = state["eligibility"]["region"]
        state["screening_queue"] = build_initial_queue(region)
        state["screening_step"] = 0
        state["awaiting_answer"] = False
        state["current_question"] = None

    queue = state["screening_queue"]
    step = state["screening_step"]


    # COMPLETE

    if step >= len(queue):
        state["current_node"] = "quote_agent"
        await send({
            "type": "stream",
            "text": "Screening complete. Moving to quote.\n",
        })
        return state


    # ASK QUESTION

    if not state.get("awaiting_answer"):
        current = queue[step]

        await send({
            "type": "stream",
            "text": QUESTIONS[current] + "\n",
        })

        state["current_question"] = current
        state["awaiting_answer"] = True
        return state


    # IGNORE EMPTY INPUT

    if not user_text:
        return state

    text = user_text.strip().lower()
    current = state["current_question"]


    # MAIN QUESTION PROCESSING


    # ---------- TOBACCO ----------
    if current == "tobacco":
        if text not in {"yes", "no"}:
            await send({
                "type": "stream",
                "text": "Answer yes or no.\n"
            })
            return state

        answers["tobacco_current"] = (text == "yes")

    # ---------- PREEXISTING ----------
    elif current == "preexisting":
        if text not in {"yes", "no"}:
            await send({
                "type": "stream",
                "text": "Answer yes or no.\n"
            })
            return state

        answers["preexisting_conditions"] = (text == "yes")

    # ---------- EXERCISE ----------
    elif current == "exercise":
        try:
            answers["exercise_hours_per_week"] = float(text)
        except ValueError:
            await send({
                "type": "stream",
                "text": "Enter a number.\n"
            })
            return state


    state["awaiting_answer"] = False
    state["screening_step"] += 1

    return await handle(
        user_text="",
        state=state,
        user=user,
        send=send
    )
