"""Generate mock JWTs for the three test users.

Usage:
    python3 mint_tokens.py

Requires:
    pip install pyjwt python-dotenv

Reads JWT_SECRET from .env. Tokens are valid for 7 days.
"""

import os
import time

try:
    import jwt
except ImportError:
    raise SystemExit("pyjwt not installed. Run: pip install pyjwt python-dotenv")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional; env vars can come from the shell

SECRET = os.getenv("JWT_SECRET", "change-me-in-env")
ALGO = "HS256"
TTL_SECONDS = 7 * 24 * 60 * 60

USERS = [
    {"sub": "app-001",   "email": "applicant@test.com", "role": "applicant"},
    {"sub": "agent-002", "email": "agent@test.com",     "role": "agent"},
    {"sub": "admin-003", "email": "admin@test.com",     "role": "admin"},
]


def main() -> None:
    now = int(time.time())
    print(f"JWT_SECRET: {SECRET}")
    print(f"Algorithm:  {ALGO}")
    print()
    for u in USERS:
        payload = {**u, "iat": now, "exp": now + TTL_SECONDS}
        token = jwt.encode(payload, SECRET, algorithm=ALGO)
        print(f"# {u['email']}  (role={u['role']})")
        print(token)
        print()


if __name__ == "__main__":
    main()
