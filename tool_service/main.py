"""Tool service — stateless FastAPI app on port 8001.

Exposes:
  GET /tools/providers          — list of mock insurance providers
  GET /tools/pricing            — deterministic mock price (sleeps 1s)
  GET /health                   — health probe

"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Underwriting Tool Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "tool_service"}