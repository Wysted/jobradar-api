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

# --- TypeSafe JEV (L3 rerank evaluation; pipeline integration pending) ---
TYPESAFE_API_KEY: str = os.environ.get("TYPESAFE_API_KEY", "")
# Pin the exact model when calibrating thresholds; `jev-latest` may move.
JEV_MODEL: str = os.environ.get("JEV_MODEL", "jev-1.13.0")
JEV_PRICE_PER_MTOK_INPUT: float = float(os.environ.get("JEV_PRICE_PER_MTOK_INPUT", "0.042"))
# API limit is 80 req/s; one request per offer, bounded fan-out.
JEV_MAX_CONCURRENCY: int = int(os.environ.get("JEV_MAX_CONCURRENCY", "20"))
JEV_DESCRIPTION_MAX_CHARS: int = int(os.environ.get("JEV_DESCRIPTION_MAX_CHARS", "2000"))
# Requirements-focused excerpt: keep this many leading chars, then jump to the
# requirements section (long offers put it after the first 2000 chars).
JEV_DESCRIPTION_HEAD_CHARS: int = int(os.environ.get("JEV_DESCRIPTION_HEAD_CHARS", "500"))
# Composite score weights (normalized Score answers, combined in code).
JEV_WEIGHT_TECH: float = float(os.environ.get("JEV_WEIGHT_TECH", "0.40"))
JEV_WEIGHT_AREA: float = float(os.environ.get("JEV_WEIGHT_AREA", "0.20"))
JEV_WEIGHT_ROLE: float = float(os.environ.get("JEV_WEIGHT_ROLE", "0.20"))
JEV_WEIGHT_SENIORITY: float = float(os.environ.get("JEV_WEIGHT_SENIORITY", "0.20"))
# Routing thresholds: discard without LLM only with high confidence.
JEV_BLOCKER_NOUL_THRESHOLD: float = float(os.environ.get("JEV_BLOCKER_NOUL_THRESHOLD", "0.85"))
JEV_CHOICE_BLOCKER_CONFIDENCE: float = float(os.environ.get("JEV_CHOICE_BLOCKER_CONFIDENCE", "0.80"))
JEV_DISCARD_SCORE: int = int(os.environ.get("JEV_DISCARD_SCORE", "35"))
# v3 discard gate: composite recomputed with each Score at this upper quantile of
# its distribution must still be below JEV_DISCARD_SCORE (replaces min confidence).
JEV_DISCARD_UPPER_QUANTILE: float = float(os.environ.get("JEV_DISCARD_UPPER_QUANTILE", "0.90"))
# Code-side numeric checks (never asked to JEV).
JEV_YEARS_GAP_TOLERANCE: float = float(os.environ.get("JEV_YEARS_GAP_TOLERANCE", "1.0"))
# Soft penalty: on a years gap, cap the seniority fit instead of zeroing it.
JEV_YEARS_GAP_SENIORITY_FIT: float = float(os.environ.get("JEV_YEARS_GAP_SENIORITY_FIT", "0.25"))
# The current LLM prompt does not treat languages as blockers; keep language_gap
# informative until the user decides otherwise.
JEV_LANGUAGE_GAP_BLOCKS: bool = os.environ.get("JEV_LANGUAGE_GAP_BLOCKS", "false").lower() == "true"

# --- Refresh pipeline (public POST /api/v1/pipeline/refresh) ---
REFRESH_QUERIES: list[str] = [
    q.strip()
    for q in os.environ.get(
        "REFRESH_QUERIES",
        "desarrollador full stack,desarrollador backend,desarrollador frontend",
    ).split(",")
    if q.strip()
]
REFRESH_LOCATION: str = os.environ.get("REFRESH_LOCATION", "Chile")
REFRESH_MAX_PAGES: int = int(os.environ.get("REFRESH_MAX_PAGES", "2"))

CV_BATCH_CONCURRENCY: int = int(os.environ.get("CV_BATCH_CONCURRENCY", "5"))
CV_OUTPUT_DIR: Path = Path(os.environ.get("CV_OUTPUT_DIR", "/app/output/cv"))
CV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
