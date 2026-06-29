"""config.py — single source of truth for app configuration.

Everything is read from environment variables (loaded from .env in local dev via
python-dotenv). No secrets or hosts are hardcoded. retrieval.py / ingest.py /
api.py all import from here so there is exactly one config system.
"""

import os

from dotenv import load_dotenv

# Load .env if present (no-op in Docker where env vars are injected directly).
load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# ---- MySQL -----------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 3306)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "stripe_rag")

# Connection kwargs WITHOUT a database selected — used for health checks and for
# bootstrapping before the schema exists (phase b creates the database).
DB_CONFIG_NO_DB = dict(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
)

# Full connection kwargs, with the database selected (normal app use).
DB_CONFIG = dict(DB_CONFIG_NO_DB, database=DB_NAME)


# ---- Ollama (local only) ---------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")


# ---- Flask API -------------------------------------------------------------
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = _int("API_PORT", 5000)
