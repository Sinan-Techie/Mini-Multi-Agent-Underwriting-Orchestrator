"""eligibility agent: collects age and region, validates, updates state."""

from typing import Callable, Awaitable
from ..state import SessionState
from ..auth import AuthUser
from . import screening as screening_agent

VALID_REGIONS = {"UAE", "KSA", "IND"}


def _parse_input(text: str):
    """
    Very simple parser:
    expects something like: '25 IND'
    """
    parts = text.strip().upper().split()
    if len(parts) != 2:
        return None, None

    age_raw, region = parts

    try:
        age = int(age_raw)
    except ValueError:
        return None, None

    return age, region


async def handle(
    *,
    user_text: str,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
    trace_id: str,
):
    """
    EligibilityAgent:
    - collects age + region
    - validates
    - updates state
    """

    # If eligibility not yet filled → ask question
    if not state["eligibility"] and not user_text:
        await send({
            "type": "stream",
            "text": "Please provide your age and region (UAE, KSA, IND).\nExample: 30 IND\n",
        })
        return state

    # Parse input
    age, region = _parse_input(user_text)

    if age is None or region is None:
        await send({
            "type": "stream",
            "text": "Invalid input. Please provide in format: <age> <region> (e.g., 30 IND)\n",
        })
        return state

    # Validate age
    if not (18 <= age <= 75):
        await send({
            "type": "stream",
            "text": "Age must be between 18 and 75. Please try again.\n",
        })
        return state

    # Validate region
    if region not in VALID_REGIONS:
        await send({
            "type": "stream",
            "text": "Region must be one of UAE, KSA, IND. Please try again.\n",
        })
        return state

    # Success → update state
    state["eligibility"] = {"age": age, "region": region}
    state["current_node"] = "health_screening_agent"

    await send({
        "type": "stream",
        "text": f"Got it. Age: {age}, Region: {region}. Moving to health screening.\n",
    })

    await send({
            "type": "node",
            "name": "health_screening_agent",
        })
    state = await screening_agent.handle(
        user_text="",
        state=state,
        user=user,
        send=send,
        trace_id=trace_id,  
    )    

    return state