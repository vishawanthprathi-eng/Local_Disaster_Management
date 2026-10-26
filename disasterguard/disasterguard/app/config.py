import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
if not OLLAMA_HOST.startswith("http"):
    OLLAMA_HOST = "http://" + OLLAMA_HOST
OLLAMA_MODEL = os.environ.get("DG_MODEL", "gemma3:4b")
LLM_TIMEOUT = float(os.environ.get("DG_LLM_TIMEOUT", "120"))
DB_PATH = os.environ.get("DG_DB", str(BASE_DIR.parent / "data" / "disasterguard.db"))
HOST = os.environ.get("DG_HOST", "127.0.0.1")
PORT = int(os.environ.get("DG_PORT", "8000"))
