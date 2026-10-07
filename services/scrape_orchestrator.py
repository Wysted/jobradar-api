import logging
from collections.abc import Awaitable, Callable

from database import async_session
from models.database.pipeline_run import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING
from services import bne as bne_service
from services import chiletrabajos as chiletrabajos_service
from services import getonbrd as getonbrd_service
from services import linkedin as linkedin_service
from services import pipeline_runs

logger = logging.getLogger(__name__)

PLATFORM_RUNNERS = {
    "linkedin": linkedin_service.run,
    "chiletrabajos": chiletrabajos_service.run,
    "getonbrd": getonbrd_service.run,
    "bne": bne_service.run,
}


async def scrape_query(
    session,
    *,
    query: str | None,
    max_pages: int,
    location: str | None,
    platforms: list[str] | None = None,
    on_platform: Callable[[str, dict], Awaitable[None]] | None = None,
) -> dict[str, dict]:
    """Scrape one query on every platform; a platform failure is recorded, not raised."""
    per_platform: dict[str, dict] = {}
    for platform in platforms or PLATFORM_RUNNERS.keys():
        try:
            result = await PLATFORM_RUNNERS[platform](
                session, query=query, max_pages=max_pages, location=location
            )
        except Exception as exc:
            logger.exception("scrape failed platform=%s query=%s", platform, query)
            try:
                await session.rollback()
            except Exception:
                logger.exception("rollback after scrape failure also failed")
            result = {"error": str(exc)}
        per_platform[platform] = result
        if on_platform is not None:
            await on_platform(platform, result)
    return per_platform


async def run_scrape(
    run_id,
    platforms: list[str] | None,
    query: str | None,
    max_pages: int = 1,
    location: str | None = None,
) -> None:
    async with async_session() as session:
        run = await pipeline_runs.get_run(session, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run {run_id} not found")

        await pipeline_runs.transition(session, run, STATUS_RUNNING)

        stats: dict = {"per_platform": {}, "total_inserted": 0}

        async def on_platform(platform: str, result: dict) -> None:
            stats["per_platform"][platform] = result
            stats["total_inserted"] += result.get("jobs_inserted", 0)

        await scrape_query(
            session,
            query=query,
            max_pages=max_pages,
            location=location,
            platforms=platforms,
            on_platform=on_platform,
        )

        all_failed = all(
            "error" in v for v in stats["per_platform"].values()
        ) and stats["per_platform"]

        if all_failed:
            await pipeline_runs.transition(
                session,
                run,
                STATUS_FAILED,
                stats=stats,
                error=f"all platforms failed: {list(stats['per_platform'].keys())}",
            )
        else:
            await pipeline_runs.transition(session, run, STATUS_COMPLETED, stats=stats)
