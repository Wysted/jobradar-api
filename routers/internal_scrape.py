from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_internal_key
from services import bne as bne_service
from services import chiletrabajos as chiletrabajos_service
from services import getonbrd as getonbrd_service
from services import linkedin as linkedin_service

PLATFORM_RUNNERS = {
    "linkedin": linkedin_service.run,
    "chiletrabajos": chiletrabajos_service.run,
    "getonbrd": getonbrd_service.run,
    "bne": bne_service.run,
}

router = APIRouter(
    prefix="/internal/jobs",
    tags=["internal-scrape"],
    dependencies=[Depends(require_internal_key)],
)


@router.get("/{platform}", status_code=status.HTTP_200_OK)
async def scrape_platform(
    platform: Literal["linkedin", "chiletrabajos", "getonbrd", "bne"],
    query: str | None = Query(default=None),
    max_pages: int = Query(default=1, ge=1, le=10),
    session: AsyncSession = Depends(get_db),
):
    runner = PLATFORM_RUNNERS[platform]
    return await runner(session, query=query, max_pages=max_pages)
