from typing import Callable, Awaitable
from ..state import SessionState
from ..auth import AuthUser
from ..llm.groq_provider import GroqProvider
from observability import log
import time
# import asyncio

from fastapi import WebSocketDisconnect

from ..tool_client import (
    get_providers,
    get_all_pricing_parallel
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
    - fetch providers and pricing (skipped if prices already in state)
    - pick cheapest
    - stream recommendation, accumulating full text into state
    """

    llm = GroqProvider()
    age = state["eligibility"]["age"]
    region = state["eligibility"]["region"]

    # IF already completed — replay saved recommendation.
    if state.get("quote") and state.get("quote_streamed"):
        await send({
            "type": "stream",
            "text": "Your quote has already been generated. Here is your recommendation:\n\n",
        })
        await send({
            "type": "stream",
            "text": state.get("quote_recommendation", ""),
        })
        return state

    # Prices already fetched but stream was interrupted — regenerate quote
    if state.get("quote"):
        prices = state["quote"]["all_prices"]

    else:

        providers = await get_providers(
            jwt_token=user.token,
            trace_id=trace_id,
            session_id=state["session_id"],
        )

        start = time.time()

        prices = await get_all_pricing_parallel(
            providers=providers,
            age=age,
            region=region,
            jwt_token=user.token,
            trace_id=trace_id,
            session_id=state["session_id"],
        )

        elapsed_ms = int((time.time() - start) * 1000)
        log(
            trace_id=trace_id,
            session_id=state["session_id"],
            node="quote_agent",
            event="tool_call",
            latency_ms=elapsed_ms,
            extra={"detail": "parallel pricing fetch complete"},
        )

        if not prices:
            await send({
                "type": "error",
                "code": "tool_unavailable",
                "message": "No pricing data available.",
            })
            return state

        best_provider = min(prices, key=prices.get)
        best_price = prices[best_provider]

        state["quote"] = {
            "provider": best_provider,
            "price": best_price,
            "all_prices": prices,
        }
        state["quote_streamed"] = False
        state["quote_recommendation"] = ""

    best_provider = state["quote"]["provider"]
    best_price = state["quote"]["price"]

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

    # Stream response — accumulate tokens so we can replay on reconnect.
    accumulated = ""

    try:
        async for token in llm.stream(messages):
            accumulated += token
            await send({"type": "stream", "text": token})
            # await asyncio.sleep(0.03)  # Use if you want slower streaming for testing

    except WebSocketDisconnect:
        raise

    except Exception as exc:
        # LLM provider error — send fallback.
        await send({"type": "error", "code": "llm_failed", "message": str(exc)})
        fallback = f"Recommended: {best_provider} at ${best_price}/year.\n"
        await send({"type": "stream", "text": fallback})
        accumulated = fallback

    # Streaming done — mark complete and persist the full text.
    state["quote_recommendation"] = accumulated
    state["quote_streamed"] = True

    return state