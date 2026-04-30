"""RBAC enforcement for the tool service.

The tool service verifies the JWT 

Permissions:
    applicant  : providers only
    agent      : providers + pricing
    admin      : providers + pricing + traces

Raises HTTP 403 with a clear message when role lacks permission.
"""

import jwt
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import JWT_SECRET, JWT_ALGORITHM


# Permission table — single source of truth for RBAC

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "applicant": {"providers"},
    "agent":     {"providers", "pricing"},
    "admin":     {"providers", "pricing", "traces"},
}

bearer_scheme = HTTPBearer()


def _decode_jwt(token: str) -> dict:
    """Decode and verify JWT. Raises 401 on any failure."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token has expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")


def require_permission(tool: str):
    """Returns the decoded JWT payload so the endpoint can use it if needed.
    Raises HTTP 401 if token is bad, HTTP 403 if role lacks permission.
    """
    def _check(
        creds: HTTPAuthorizationCredentials = Security(bearer_scheme),
    ) -> dict:
        payload = _decode_jwt(creds.credentials)
        role = payload.get("role", "")
        allowed = ROLE_PERMISSIONS.get(role, set())

        if tool not in allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"role '{role}' is not permitted to access tool '{tool}'. "
                    f"Allowed tools for this role: {sorted(allowed)}"
                ),
            )
        return payload

    return _check