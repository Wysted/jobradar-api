import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import config
from models.database.app_setting import KEY_SEARCH, AppSetting
from models.database.platform import Platform
from services.scrape_orchestrator import PLATFORM_RUNNERS

logger = logging.getLogger(__name__)


@dataclass
class SearchSettings:
    queries: list[str]
    location: str
    max_pages: int
    platforms: list[str]


def default_search_settings() -> SearchSettings:
    """Defaults come from the REFRESH_* env vars and every registered platform."""
    return SearchSettings(
        queries=list(config.REFRESH_QUERIES),
        location=config.REFRESH_LOCATION,
        max_pages=config.REFRESH_MAX_PAGES,
        platforms=list(PLATFORM_RUNNERS.keys()),
    )


async def get_search_settings(
    session: AsyncSession,
) -> tuple[SearchSettings, datetime | None]:
    """Return persisted settings (with updated_at) or defaults (updated_at None)."""
    row = (
        await session.execute(select(AppSetting).where(AppSetting.key == KEY_SEARCH))
    ).scalar_one_or_none()
    if row is None:
        return default_search_settings(), None
    try:
        value = row.value
        platforms = [p for p in value["platforms"] if p in PLATFORM_RUNNERS]
        if not platforms or not value["queries"]:
            raise ValueError("empty queries or platforms")
        settings = SearchSettings(
            queries=list(value["queries"]),
            location=str(value["location"]),
            max_pages=int(value["max_pages"]),
            platforms=platforms,
        )
    except (KeyError, TypeError, ValueError):
        logger.exception("invalid stored search settings; using defaults")
        return default_search_settings(), None
    return settings, row.updated_at


async def save_search_settings(
    session: AsyncSession, settings: SearchSettings
) -> datetime:
    now = datetime.now(timezone.utc)
    stmt = pg_insert(AppSetting).values(
        key=KEY_SEARCH, value=asdict(settings), updated_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    await session.execute(stmt)
    await session.commit()
    return now


async def list_available_platforms(session: AsyncSession) -> list[Platform]:
    result = await session.execute(
        select(Platform)
        .where(Platform.name.in_(list(PLATFORM_RUNNERS.keys())))
        .order_by(Platform.display_name)
    )
    return list(result.scalars().all())
