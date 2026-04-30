"""Shared configuration — loaded by both orchestrator and tool service."""
 
import os
from dotenv import load_dotenv
 
load_dotenv()
 
JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM: str = "HS256"
 
TOOL_SERVICE_URL: str = os.getenv("TOOL_SERVICE_URL", "http://localhost:8001")
 
STATE_BACKEND: str = os.getenv("STATE_BACKEND", "sqlite")
STATE_SQLITE_PATH: str = os.getenv("STATE_SQLITE_PATH", "./state.db")
SESSION_TTL_SECONDS: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
 
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

TRACES_DIR = os.getenv("TRACES_DIR", "./traces")