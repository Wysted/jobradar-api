import secrets

from fastapi import Header, HTTPException, status

import config

# Read keys at import time — fail fast if missing in production.
# In development these may be empty strings (docker-compose sets them).
_INTERNAL_API_KEY: str = config.INTERNAL_API_KEY
_API_KEY: str = config.API_KEY


async def require_internal_key(
    x_internal_api_key: str | None = Header(default=None),
) -> None:
    """Dependency for internal (n8n / cron) endpoints.

    Missing header → 401 (not FastAPI's default 422) to honor the spec contract.
    Compares using secrets.compare_digest to prevent timing-oracle attacks.
    """
    if x_internal_api_key is None or not secrets.compare_digest(
        x_internal_api_key, _INTERNAL_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid internal API key.",
        )


async def require_api_key(
    x_api_key: str | None = Header(default=None),
) -> None:
    """Dependency for public client-facing endpoints."""
    if x_api_key is None or not secrets.compare_digest(x_api_key, _API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
