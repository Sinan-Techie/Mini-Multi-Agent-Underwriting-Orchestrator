"""
nodes/quote.py

Replaces agents/quote.py.

This node does NOT use interrupt() — quote generation is non-interactive.
The WS layer streams tokens directly to the client via a custom stream
callback stored in the LangGraph config (see main.py).

Original's three resume paths are preserved:
    1. quote_streamed = True  →  replay saved recommendation
    2. quote set, not streamed →  skip API calls, re-run LLM
    3. fresh  →  fetch providers + parallel pricing, then LLM

Streaming approach:
    main.py passes `send` into the graph config so this node can push
    tokens to the WebSocket while still being a normal LangGraph node.

    config["configurable"]["send"] = send   # set in ws_chat()
    send = config["configurable"]["send"]   # read here
"""

import asyncio
import time
from typing import Callable, Awaitable

from langchain_core.runnables import RunnableConfig

from ..state import UnderwritingState, QuoteData
from ..llm.groq_provider import GroqProvider
from ..tool_client import (
    get_providers,
    get_all_pricing_parallel,
    ToolForbiddenError,
    ToolUnavailableError,
)
from observability import log


async def quote_node(
    state: UnderwritingState,
    config: RunnableConfig,
) -> dict:
    """
    Fetch pricing in parallel, stream an LLM recommendation, persist result.

    The `send` callable and `user` object are threaded in via config so
    this node can push to the WebSocket without importing the WS layer.
    """
    cfg        = config["configurable"]
    send: Callable[[dict], Awaitable[None]] = cfg["send"]
    jwt_token: str  = cfg["jwt_token"]
    trace_id: str   = cfg["trace_id"]
    session_id: str = state["session_id"]

    llm = GroqProvider()
    age    = state["eligibility"]["age"]
    region = state["eligibility"]["region"]

    # ── Path 1: already fully complete — replay ───────────────────────────────
    if state.get("quote") and state.get("quote_streamed"):
        await send({"type": "stream", "text": "Your quote has already been generated:\n\n"})
        await send({"type": "stream", "text": state["quote_recommendation"]})
        return {}   # state unchanged

    # ── Path 2: prices fetched, stream was interrupted — just re-run LLM ─────
    if state.get("quote"):
        prices = state["quote"]["all_prices"]

    # ── Path 3: fresh — fetch everything ─────────────────────────────────────
    else:
        try:
            providers = await get_providers(
                jwt_token=jwt_token,
                trace_id=trace_id,
                session_id=session_id,
            )

            t0 = time.time()
            prices = await get_all_pricing_parallel(
                providers=providers,
                age=age,
                region=region,
                jwt_token=jwt_token,
                trace_id=trace_id,
                session_id=session_id,
            )
            log(
                trace_id=trace_id,
                session_id=session_id,
                node="quote_node",
                event="tool_call",
                latency_ms=int((time.time() - t0) * 1000),
                extra={"detail": "parallel pricing fetch complete"},
            )

        except (ToolForbiddenError, ToolUnavailableError):
            # Re-raise — main.py catches these and emits typed error frames
            raise

        if not prices:
            await send({
                "type": "error",
                "code": "tool_unavailable",
                "message": "No pricing data available.",
            })
            return {}

        best_provider = min(prices, key=prices.get)
        best_price    = prices[best_provider]

        # Persist prices so a mid-stream disconnect can resume at Path 2
        partial_quote = QuoteData(
            provider=best_provider,
            price=best_price,
            all_prices=prices,
        )
        # We can't update checkpointer mid-node, but we store in local var
        # and return it at the end so the checkpointer saves after the node.

    best_provider = (state.get("quote") or {}).get("provider") or min(prices, key=prices.get)
    best_price    = (state.get("quote") or {}).get("price")    or prices[best_provider]

    # ── LLM streaming ─────────────────────────────────────────────────────────
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
                f"User profile:\n- Age: {age}\n- Region: {region}\n\n"
                "Quotes:\n"
                + "\n".join(f"{p}: ${pr}" for p, pr in prices.items())
            ),
        },
    ]

    accumulated = ""
    try:
        async for token in llm.stream(messages):
            accumulated += token
            await send({"type": "stream", "text": token})
            await asyncio.sleep(0.03)

    except Exception as exc:
        await send({"type": "error", "code": "llm_failed", "message": str(exc)})
        fallback = f"Recommended: {best_provider} at ${best_price}/year.\n"
        await send({"type": "stream", "text": fallback})
        accumulated = fallback

    # ── Return final state ────────────────────────────────────────────────────
    return {
        "quote": QuoteData(
            provider=best_provider,
            price=best_price,
            all_prices=prices,
        ),
        "quote_recommendation": accumulated,
        "quote_streamed": True,
    }