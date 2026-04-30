"""Tool service — stateless FastAPI app on port 8001.

Endpoints:
  GET /health                       — health probe (no auth)
  GET /tools/providers              — list providers
  GET /tools/pricing?provider=&age=&region=  — mock price  (access to agent and admin roles only)

RBAC is enforced HERE via JWT forwarded in Authorization: Bearer <token>.

Each /tools/pricing call sleeps 1 s
"""

import asyncio

from fastapi import FastAPI, Query, Security, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .rbac import require_permission
from .pricing import calculate_price, PROVIDERS

app = FastAPI(title="Underwriting Tool Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



# Health

@app.get("/health")
async def health():
    return {"status": "ok", "service": "tool_service"}



# GET /tools/providers
# Allowed: applicant, agent, admin

@app.get("/tools/providers")
async def get_providers(
    _payload: dict = Security(require_permission("providers")),
):
    """Return the list of available insurance providers."""
    return {"providers": PROVIDERS}


# GET /tools/pricing
# Allowed: agent, admin   (applicant → 403)

@app.get("/tools/pricing")
async def get_pricing(
    provider: str = Query(..., description="One of: acme, globex, initech"),
    age: int     = Query(..., ge=18, le=75, description="Applicant age"),
    region: str  = Query(..., description="One of: UAE, KSA, IND"),
    _payload: dict = Security(require_permission("pricing")),
):
    """
    Return a deterministic annual premium for the given provider/age/region.
    Simulates real API latency with a 1-second sleep.
    QuoteAgent calls this endpoint 3x in parallel so total is approx 1s not 3s.
    """
    await asyncio.sleep(1)

    try:
        price = calculate_price(provider=provider, age=age, region=region)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "provider": provider,
        "age": age,
        "region": region,
        "annual_premium_usd": price,
        "currency": "USD",
    }