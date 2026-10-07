import logging
from dataclasses import asdict
from time import monotonic

from openai import AuthenticationError, PermissionDeniedError

from database import async_session
from models.database.pipeline_run import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING
from services import pipeline_runs
from services.embedding_backfill import embed_pending
from services.ranking.pipeline import execute_ranking
from services.scrape_orchestrator import scrape_query
from services.search_settings import get_search_settings

logger = logging.getLogger(__name__)

STAGE_SCRAPE = "scrape"
STAGE_EMBED = "embed"
STAGE_RANK = "rank"
STAGE_DONE = "done"

EMBED_BATCH_SIZE = 100

OPENAI_KEY_ERROR = "OpenAI API key inválida o revocada"


def _user_error(exc: BaseException, stage: str | None) -> str:
    """User-presentable error text, unwrapping the cause of batch errors."""
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, (AuthenticationError, PermissionDeniedError)):
            return OPENAI_KEY_ERROR
    return f"{stage}: {exc}" if stage else str(exc)


async def _flush_stats(session, run, stats: dict) -> None:
    """Persist incremental stats without FSM transition (run stays RUNNING)."""
    run.stats = dict(stats)
    await session.commit()
    await session.refresh(run)


async def run_refresh(run_id) -> None:
    """Chain scrape (all queries/platforms) -> embed backfill -> rank in ONE run."""
    async with async_session() as session:
        run = await pipeline_runs.get_run(session, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run {run_id} not found")

        await pipeline_runs.transition(session, run, STATUS_RUNNING)
        t0 = monotonic()

        stats: dict = {
            **(run.stats or {}),
            "stage": STAGE_SCRAPE,
            "current_query": None,
            "total_queries": 0,
            "per_query": {},
            "total_inserted": 0,
            "jobs_embedded": 0,
            "candidates_after_filter": 0,
            "new_scores": 0,
        }

        try:
            # Read the user's search settings once at run start (defaults if unset).
            search, _ = await get_search_settings(session)
            stats["total_queries"] = len(search.queries)
            stats["settings"] = asdict(search)
            await _flush_stats(session, run, stats)

            # Stage 1: scrape. A failure in one platform/query is recorded, not fatal.
            for query in search.queries:
                stats["current_query"] = query
                per_platform: dict = stats["per_query"].setdefault(query, {})
                await _flush_stats(session, run, stats)

                async def on_platform(platform: str, result: dict) -> None:
                    per_platform[platform] = result
                    stats["total_inserted"] += result.get("jobs_inserted", 0)
                    await _flush_stats(session, run, stats)

                await scrape_query(
                    session,
                    query=query,
                    max_pages=search.max_pages,
                    location=search.location or None,
                    platforms=search.platforms,
                    on_platform=on_platform,
                )

            stats["current_query"] = None

            # Stage 2: embedding backfill. Failure fails the run.
            stats["stage"] = STAGE_EMBED
            await _flush_stats(session, run, stats)
            embed_stats = await embed_pending(
                session, batch_size=EMBED_BATCH_SIZE, max_jobs=None
            )
            stats["jobs_embedded"] = embed_stats["jobs_embedded"]
            await _flush_stats(session, run, stats)

            # Stage 3: ranking of new, unscored jobs. Failure fails the run.
            stats["stage"] = STAGE_RANK
            await _flush_stats(session, run, stats)

            async def on_rank_progress(rank_stats: dict) -> None:
                stats["candidates_after_filter"] = rank_stats["candidates_after_filter"]
                stats["new_scores"] = rank_stats["new_scores"]
                await _flush_stats(session, run, stats)

            await execute_ranking(
                session,
                only_new=True,
                skip_already_scored=True,
                on_progress=on_rank_progress,
            )

            stats["stage"] = STAGE_DONE
            stats["duration_s"] = round(monotonic() - t0, 2)
            await pipeline_runs.transition(session, run, STATUS_COMPLETED, stats=stats)

        except Exception as exc:
            logger.exception("refresh pipeline failed run=%s stage=%s", run_id, stats.get("stage"))
            try:
                await session.rollback()
            except Exception:
                logger.exception("rollback after refresh failure also failed")
            stats["duration_s"] = round(monotonic() - t0, 2)
            try:
                await pipeline_runs.transition(
                    session, run, STATUS_FAILED, stats=stats, error=_user_error(exc, stats.get("stage"))
                )
            except Exception:
                logger.exception("could not persist FAILED status for run=%s", run_id)
            raise
