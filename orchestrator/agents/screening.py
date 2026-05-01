from typing import Callable, Awaitable
from ..state import SessionState, save_state
from ..auth import AuthUser
from . import quote as quote_agent


QUESTIONS = {
    "tobacco": "Do you currently use any form of tobacco? (yes/no)",
    "preexisting": "Have you had diabetes, heart disease, cancer, or respiratory illness? (yes/no)",
    "exercise": "How many hours per week do you exercise?",
}


# QUEUE BUILDER

def build_initial_queue() -> list[str]:
    return [
        "tobacco",
        "preexisting",
        "exercise",
    ]

async def handle(
    *,
    user_text: str,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
    trace_id: str,
):
    answers = state["screening_answers"]

    # INIT

    if not state["screening_queue"]:
        region = state["eligibility"]["region"]
        state["screening_queue"] = build_initial_queue()
        state["screening_step"] = 0
        state["awaiting_answer"] = False
        state["current_question"] = None

    queue = state["screening_queue"]
    step = state["screening_step"]


    # COMPLETE

    if step >= len(queue):
        state["current_node"] = "quote_agent"
        save_state(user.session_id, state)
        await send({
            "type": "stream",
            "text": "Screening complete. Generating quote...\n",
        })
        await send({"type": "done"})

        await send({
                "type": "node",
                "name": "quote_agent",
            })
        state = await quote_agent.handle(
            state=state,
            user=user,
            send=send,
            trace_id=trace_id,  
        )
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
                "text": "On average, how many hours per week do you exercise? Enter a number in hours (for example: 0, 1.5, 3).\n"
            })
            return state


    state["awaiting_answer"] = False
    state["screening_step"] += 1

    return await handle(
        user_text="",
        state=state,
        user=user,
        send=send,
        trace_id=trace_id, 
    )
