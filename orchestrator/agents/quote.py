from typing import Callable, Awaitable
from ..state import SessionState
from ..auth import AuthUser
from ..llm.groq_provider import GroqProvider
import time
import asyncio

from ..tool_client import (
    get_providers,
    get_all_pricing_parallel,
    ToolForbiddenError,
    ToolUnavailableError,
)


async def handle(
    *,
    state: SessionState,
    user: AuthUser,
    send: Callable[[dict], Awaitable[None]],
    trace_id: str,
):
    """
    QuoteAgent:
    - fetch providers
    - fetch pricing in parallel
    - pick cheapest
    - stream recommendation
    """

    llm = GroqProvider()

    if state["quote"]:
        await send({
            "type": "stream",
            "text": "Quote already generated.\n",
        })
        return state

    age = state["eligibility"]["age"]
    region = state["eligibility"]["region"]

    try:
        # 1. Get providers
        providers = await get_providers(
            jwt_token=user.token,  
            trace_id=trace_id,
            session_id=state["session_id"],
        )

        start = time.time()

        # 2. Get pricing in parallel
        prices = await get_all_pricing_parallel(
            providers=providers,
            age=age,
            region=region,
            jwt_token=user.token,  
            trace_id=trace_id,
            session_id=state["session_id"],
        )

        print("Elapsed:", time.time() - start)

    except ToolForbiddenError as exc:
        raise exc  # handled in orchestrator

    except ToolUnavailableError as exc:
        raise exc

    if not prices:
        await send({
            "type": "error",
            "code": "tool_unavailable",
            "message": "No pricing data available.",
        })
        return state

    # 3. Pick cheapest
    best_provider = min(prices, key=prices.get)
    best_price = prices[best_provider]

    # Save to state
    state["quote"] = {
        "provider": best_provider,
        "price": best_price,
        "all_prices": prices,
    }

    # 4. Stream response (chunked)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an insurance underwriting assistant. "
                "Compare quotes and recommend the best option clearly and concisely."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User profile:\n"
                f"- Age: {age}\n"
                f"- Region: {region}\n\n"
                f"Quotes:\n"
                + "\n".join(f"{p}: ${price}" for p, price in prices.items())
            ),
        },
    ]

    try:
        async for token in llm.stream(messages):
            await send({"type": "stream", "text": token})
            await asyncio.sleep(0.03)

    except Exception as exc:
        await send({
            "type": "error",
            "code": "llm_failed",
            "message": str(exc),
        })
        fallback = (
            f"Recommended: {best_provider} at ${best_price}/year.\n"
        )
        await send({"type": "stream", "text": fallback})

    return state
