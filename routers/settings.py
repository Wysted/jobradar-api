from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_api_key
from models.settings import AvailablePlatform, SearchSettingsIn, SearchSettingsOut
from services.search_settings import (
    SearchSettings,
    get_search_settings,
    list_available_platforms,
    save_search_settings,
)

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


async def _search_out(db: AsyncSession, settings: SearchSettings, updated_at) -> SearchSettingsOut:
    platforms = await list_available_platforms(db)
    return SearchSettingsOut(
        queries=settings.queries,
        location=settings.location,
        max_pages=settings.max_pages,
        platforms=settings.platforms,
        available_platforms=[
            AvailablePlatform(name=p.name, display_name=p.display_name) for p in platforms
        ],
        updated_at=updated_at,
    )


@router.get("/search", response_model=SearchSettingsOut, status_code=status.HTTP_200_OK)
async def get_search(
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SearchSettingsOut:
    settings, updated_at = await get_search_settings(db)
    return await _search_out(db, settings, updated_at)


@router.put("/search", response_model=SearchSettingsOut, status_code=status.HTTP_200_OK)
async def put_search(
    body: SearchSettingsIn,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> SearchSettingsOut:
    settings = SearchSettings(
        queries=body.queries,
        location=body.location,
        max_pages=body.max_pages,
        platforms=body.platforms,
    )
    updated_at = await save_search_settings(db, settings)
    return await _search_out(db, settings, updated_at)
