"""JWT verification and session management helpers.

Used by the orchestrator on every WebSocket connection.
session_id is derived deterministically from JWT sub so the same user
always resumes the same session: sha256(sub)[:16].
"""

import hashlib
from dataclasses import dataclass
import jwt
from config import JWT_SECRET, JWT_ALGORITHM


class AuthError(Exception):
    """Raised when JWT is missing, expired, or tampered with."""


@dataclass
class AuthUser:
    sub: str
    email: str
    role: str
    session_id: str  


def verify_token(token: str) -> AuthUser:
    """Decode and validate a JWT. Raises AuthError on any failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise AuthError("token has expired")
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}")

    sub = payload.get("sub")
    email = payload.get("email", "")
    role = payload.get("role", "")

    if not sub or not role:
        raise AuthError("token missing required fields: sub, role")

    session_id = _derive_session_id(sub)
    return AuthUser(sub=sub, email=email, role=role, session_id=session_id)


def _derive_session_id(sub: str) -> str:
    """Deterministic session ID: sha256(sub)[:16]."""
    return hashlib.sha256(sub.encode()).hexdigest()[:16]


def decode_token_unverified(token: str) -> dict:
    """Decode without verification — used by the tool service to read role.
    The tool service still verifies the signature separately."""
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
    )