import os
from pathlib import Path

TEMP_DIR = Path(os.environ.get("TEMP_DIR", "temp"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))

TEMP_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/scraping_masive",
)

# --- OpenAI ---
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
OPENAI_EMBEDDING_MODEL: str = os.environ.get(
    "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
)
OPENAI_RERANK_MODEL: str = os.environ.get("OPENAI_RERANK_MODEL", "gpt-4o-mini")

# --- CV generator (separado del ranker, NO mezclar) ---
OPENAI_CV_MODEL: str = os.environ.get("OPENAI_CV_MODEL", "gpt-4o-mini")
OPENAI_INGEST_MODEL: str = os.environ.get("OPENAI_INGEST_MODEL", "gpt-4o-mini")
OPENAI_DIGEST_MODEL: str = os.environ.get("OPENAI_DIGEST_MODEL", "gpt-4o-mini")

# --- Internal auth ---
INTERNAL_API_KEY: str = os.environ.get("INTERNAL_API_KEY", "")
API_KEY: str = os.environ.get("API_KEY", "")

# --- Ranking ---
RANK_TOP_K: int = int(os.environ.get("RANK_TOP_K", "50"))
RANK_MIN_SCORE: int = int(os.environ.get("RANK_MIN_SCORE", "0"))

CV_BATCH_CONCURRENCY: int = int(os.environ.get("CV_BATCH_CONCURRENCY", "5"))
CV_OUTPUT_DIR: Path = Path(os.environ.get("CV_OUTPUT_DIR", "/app/output/cv"))
CV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
