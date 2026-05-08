from functools import lru_cache

from openai import AsyncOpenAI

import config

# Model name constants for callers that need to reference them.
EMBEDDING_MODEL: str = config.OPENAI_EMBEDDING_MODEL
RERANK_MODEL: str = config.OPENAI_RERANK_MODEL


@lru_cache(maxsize=1)
def get_openai_client() -> AsyncOpenAI:
    """Return the shared AsyncOpenAI singleton.

    Constructed once on first call; lru_cache ensures a single instance for
    the lifetime of the process.  The API key is read from config (env-backed)
    and never logged.
    """
    return AsyncOpenAI(api_key=config.OPENAI_API_KEY)
