"""HTTP client for the tool service.

All orchestrator to tool service communication goes through this module.

Error handling:
    403  → raises ToolForbiddenError
    5xx  → raises ToolUnavailableError
    network error → raises ToolUnavailableError

The JWT is forwarded on every call so the tool service can enforce RBAC.
"""

import asyncio
import time

import httpx

from config import TOOL_SERVICE_URL
from observability import log



# Typed exceptions — caught in orchestrator message loop

class ToolForbiddenError(Exception):
    """Tool service returned 403 — role lacks permission."""


class ToolUnavailableError(Exception):
    """Tool service is unreachable or returned 5xx."""



# Shared async client 
_client: httpx.AsyncClient | None = None



def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=TOOL_SERVICE_URL,
            timeout=httpx.Timeout(10.0),
        )
    return _client


def _auth_headers(jwt_token: str) -> dict:
    return {"Authorization": f"Bearer {jwt_token}"}


def _raise_for_tool_error(response: httpx.Response, tool: str) -> None:
    """Convert HTTP error codes to typed exceptions."""
    if response.status_code == 403:
        detail = response.json().get("detail", "permission denied")
        raise ToolForbiddenError(f"tool '{tool}': {detail}")
    if response.status_code >= 500:
        raise ToolUnavailableError(
            f"tool '{tool}' returned {response.status_code}: {response.text[:200]}"
        )
    response.raise_for_status()



# API calls

async def get_providers(
    *,
    jwt_token: str,
    trace_id: str,
    session_id: str,
) -> list[str]:
    """Fetch the list of available insurance providers."""
    t0 = time.perf_counter()
    log(
        trace_id=trace_id,
        session_id=session_id,
        node="tool_client",
        event="tool_call",
        extra={"tool": "providers"},
    )

    try:
        resp = await get_client().get(
            "/tools/providers",
            headers=_auth_headers(jwt_token),
        )
    except httpx.TransportError as exc:
        raise ToolUnavailableError(f"tool service unreachable: {exc}") from exc

    _raise_for_tool_error(resp, "providers")

    latency_ms = int((time.perf_counter() - t0) * 1000)
    log(
        trace_id=trace_id,
        session_id=session_id,
        node="tool_client",
        event="tool_result",
        latency_ms=latency_ms,
        extra={"tool": "providers"},
    )
    return resp.json()["providers"]


async def get_pricing(
    *,
    provider: str,
    age: int,
    region: str,
    jwt_token: str,
    trace_id: str,
    session_id: str,
) -> dict:
    """
    Fetch pricing for a single provider.
    Tool service sleeps 1 s — call all providers via asyncio.gather for parallelism.
    """
    t0 = time.perf_counter()
    log(
        trace_id=trace_id,
        session_id=session_id,
        node="tool_client",
        event="tool_call",
        extra={"tool": "pricing", "provider": provider, "age": age, "region": region},
    )

    try:
        resp = await get_client().get(
            "/tools/pricing",
            params={"provider": provider, "age": age, "region": region},
            headers=_auth_headers(jwt_token),
        )
    except httpx.TransportError as exc:
        raise ToolUnavailableError(f"tool service unreachable: {exc}") from exc

    _raise_for_tool_error(resp, "pricing")

    latency_ms = int((time.perf_counter() - t0) * 1000)
    log(
        trace_id=trace_id,
        session_id=session_id,
        node="tool_client",
        event="tool_result",
        latency_ms=latency_ms,
        extra={"tool": "pricing", "provider": provider, "price": resp.json()["annual_premium_usd"]},
    )
    return resp.json()


async def get_all_pricing_parallel(
    *,
    providers: list[str],
    age: int,
    region: str,
    jwt_token: str,
    trace_id: str,
    session_id: str,
) -> dict[str, float]:
    """
    Fetch pricing from ALL providers in parallel using asyncio.gather.

    Returns: {provider_name: annual_premium_usd}
    Providers that return 403 or 5xx are excluded from results (but logged).
    If ALL providers fail, raises the last exception.
    """
    tasks = [
        get_pricing(
            provider=p,
            age=age,
            region=region,
            jwt_token=jwt_token,
            trace_id=trace_id,
            session_id=session_id,
        )
        for p in providers
    ]

    # return_exceptions=True so one failure doesn't cancel the others
    results = await asyncio.gather(*tasks, return_exceptions=True)

    prices: dict[str, float] = {}
    last_exc: Exception | None = None

    for provider, result in zip(providers, results):
        if isinstance(result, Exception):
            last_exc = result
            log(
                trace_id=trace_id,
                session_id=session_id,
                node="tool_client",
                event="error",
                extra={"tool": "pricing", "provider": provider, "error": str(result)},
            )
        else:
            prices[provider] = result["annual_premium_usd"]

    if not prices:
        # Every provider failed — surface the last error
        raise last_exc  # type: ignore[misc]

    return prices
