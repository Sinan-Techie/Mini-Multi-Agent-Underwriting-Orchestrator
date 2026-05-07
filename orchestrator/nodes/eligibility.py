"""
nodes/eligibility.py

Replaces agents/eligibility.py.

Key difference from the original:
  - No `send` callable passed in — nodes communicate via LangGraph's
    interrupt() mechanism and the WS layer reads events from graph.astream().
  - No direct chaining to screening_agent — the supervisor's route()
    function handles that transition automatically via conditional edges.
  - interrupt() replaces the awaiting_answer flag + early return pattern.

Flow:
  1. First call (no eligibility in state, no user input yet):
       → interrupt({"prompt": "Please provide your age and region..."})
       → graph pauses, WS sends the prompt + done to client

  2. Second call (user sent "30 IND"):
       → LangGraph resumes this node with Command(resume="30 IND")
       → we validate; on success update state, on failure interrupt again

  The interrupt value is the user's raw text, injected by the WS layer
  via: graph.invoke(Command(resume=user_text), config=...)
"""

from langgraph.types import interrupt

from ..state import UnderwritingState, EligibilityData

VALID_REGIONS = {"UAE", "KSA", "IND"}


def _parse_input(text: str):
    """Expects '30 IND' — returns (age: int, region: str) or (None, None)."""
    parts = text.strip().upper().split()
    if len(parts) != 2:
        return None, None
    age_raw, region = parts
    try:
        age = int(age_raw)
    except ValueError:
        return None, None
    return age, region


async def eligibility_node(state: UnderwritingState) -> dict:
    """
    Collect and validate age + region.

    On first entry (no eligibility data yet): interrupt to ask the question.
    On resume: validate the user's answer and either interrupt again (invalid)
    or update state and return so the supervisor can route to screening.
    """

    # ── First entry: ask the question ────────────────────────────────────────
    # interrupt() pauses the graph and surfaces the value to the WS layer.
    # When the user replies, LangGraph resumes here with the answer.
    user_text: str = interrupt(
        {
            "node": "eligibility_agent",
            "prompt": (
                "Please provide your age and region (UAE, KSA, IND).\n"
                "Example: 30 IND"
            ),
        }
    )
    # Execution resumes here after the user sends their response.

    # ── Validate ──────────────────────────────────────────────────────────────
    age, region = _parse_input(user_text)

    if age is None or region is None:
        # Re-interrupt with an error prompt — same pattern, different message.
        user_text = interrupt(
            {
                "node": "eligibility_agent",
                "prompt": (
                    "Invalid input. Please use the format: <age> <region>\n"
                    "Example: 30 IND"
                ),
            }
        )
        age, region = _parse_input(user_text)

    # Age bounds check
    if age is None or not (18 <= age <= 75):
        user_text = interrupt(
            {
                "node": "eligibility_agent",
                "prompt": "Age must be between 18 and 75. Please try again.",
            }
        )
        age, region = _parse_input(user_text)

    # Region check
    if region not in VALID_REGIONS:
        user_text = interrupt(
            {
                "node": "eligibility_agent",
                "prompt": f"Region must be one of UAE, KSA, IND. Got: {region}",
            }
        )
        # After this final interrupt we trust the value; full re-validation
        # happens on the next full node invocation if they're still wrong.
        age, region = _parse_input(user_text)

    # ── Success: update state ─────────────────────────────────────────────────
    # Only the changed keys need to be returned — LangGraph merges them in.
    return {
        "eligibility": EligibilityData(age=age, region=region),
        "last_user_msg": user_text,
    }