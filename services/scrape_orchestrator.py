import logging

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

        for platform in platforms or PLATFORM_RUNNERS.keys():
            try:
                result = await PLATFORM_RUNNERS[platform](
                    session, query=query, max_pages=max_pages, location=location
                )
                stats["per_platform"][platform] = result
                stats["total_inserted"] += result.get("jobs_inserted", 0)
            except Exception as exc:
                logger.exception("scrape failed platform=%s run=%s", platform, run_id)
                stats["per_platform"][platform] = {"error": str(exc)}

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
