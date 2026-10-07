import logging
from collections.abc import Awaitable, Callable
from time import monotonic

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import config
from database import async_session
from dependencies.openai_client import RERANK_MODEL, get_openai_client
from models.database.job import JobRecord
from models.database.job_score import JobScore
from models.database.pipeline_run import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING
from services import pipeline_runs
from services.profile_service import ensure_profile_embedded
from services.ranking import sql_filters, vector_search
from services.ranking.llm_reranker import rerank_jobs

logger = logging.getLogger(__name__)


async def _flush_stats(session, run, stats: dict) -> None:
    """Persist incremental stats without FSM transition (run stays RUNNING)."""
    run.stats = dict(stats)
    await session.commit()
    await session.refresh(run)


async def execute_ranking(
    session,
    *,
    only_new: bool = True,
    skip_already_scored: bool = True,
    on_progress: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """L1 filter -> L2 vector top-K -> L3 LLM rerank -> upsert scores (no run/FSM)."""
    client = get_openai_client()

    profile = await ensure_profile_embedded(session, client)

    # L1 — SQL hard filters.
    candidate_stmt = sql_filters.build_candidate_query(
        profile, only_new, skip_already_scored=skip_already_scored
    )
    candidate_result = await session.execute(candidate_stmt)
    candidates: list[JobRecord] = list(candidate_result.scalars().all())
    candidate_ids = [job.id for job in candidates]

    stats: dict = {
        "candidates_after_filter": len(candidate_ids),
        "topk_size": 0,
        "llm_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "new_scores": 0,
        "updated_scores": 0,
    }
    if on_progress is not None:
        await on_progress(stats)

    if not candidate_ids:
        return stats

    # L2 — pgvector top-K.
    topk_pairs = await vector_search.vector_topk(
        session,
        profile.embedding,
        candidate_ids,
        k=config.RANK_TOP_K,
    )
    stats["topk_size"] = len(topk_pairs)

    if not topk_pairs:
        return stats

    topk_job_ids = [pair[0] for pair in topk_pairs]
    vector_score_by_id = {pair[0]: pair[1] for pair in topk_pairs}

    job_result = await session.execute(
        select(JobRecord).where(JobRecord.id.in_(topk_job_ids))
    )
    jobs_by_id = {job.id: job for job in job_result.scalars().all()}

    # Ordered list of jobs matching topk_pairs ordering (vector-sim rank).
    ordered_jobs: list[JobRecord] = [
        jobs_by_id[jid] for jid, _ in topk_pairs if jid in jobs_by_id
    ]

    # L3 — LLM rerank, batched (~10 offers per call). Reduces cost by
    # ~70% via profile prefix reuse + automatic prompt caching.
    from services.ranking.llm_reranker import DEFAULT_BATCH_SIZE

    scored_pairs = await rerank_jobs(
        client,
        profile,
        ordered_jobs,
        min_score=config.RANK_MIN_SCORE,
    )
    stats["llm_calls"] = (len(ordered_jobs) + DEFAULT_BATCH_SIZE - 1) // DEFAULT_BATCH_SIZE

    for job, llm_output in scored_pairs:
        vector_score = vector_score_by_id.get(job.id, 0.0)
        upsert_stmt = (
            pg_insert(JobScore)
            .values(
                job_id=job.id,
                profile_id=profile.id,
                vector_score=vector_score,
                llm_score=llm_output.score_0_100,
                matched_areas=[m.model_dump() for m in llm_output.matched_areas],
                matched_techs=[m.model_dump() for m in llm_output.matched_techs],
                missing_skills=llm_output.missing_skills,
                red_flags=llm_output.red_flags,
                pitch_angles=llm_output.pitch_angles,
                reasoning=llm_output.reasoning,
                llm_model=RERANK_MODEL,
            )
            .on_conflict_do_update(
                index_elements=["job_id"],
                set_={
                    "profile_id": profile.id,
                    "vector_score": vector_score,
                    "llm_score": llm_output.score_0_100,
                    "matched_areas": [m.model_dump() for m in llm_output.matched_areas],
                    "matched_techs": [m.model_dump() for m in llm_output.matched_techs],
                    "missing_skills": llm_output.missing_skills,
                    "red_flags": llm_output.red_flags,
                    "pitch_angles": llm_output.pitch_angles,
                    "reasoning": llm_output.reasoning,
                    "llm_model": RERANK_MODEL,
                },
            )
            .returning(JobScore.id)
        )
        await session.execute(upsert_stmt)
        stats["new_scores"] += 1

    await session.commit()
    if on_progress is not None:
        await on_progress(stats)
    return stats


async def run_ranking(
    run_id, *, only_new: bool = True, skip_already_scored: bool = True
) -> dict:
    async with async_session() as session:
        run = await pipeline_runs.get_run(session, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run {run_id} not found")

        await pipeline_runs.transition(session, run, STATUS_RUNNING)
        t0 = monotonic()

        try:
            async def on_progress(stats: dict) -> None:
                await _flush_stats(session, run, stats)

            stats = await execute_ranking(
                session,
                only_new=only_new,
                skip_already_scored=skip_already_scored,
                on_progress=on_progress,
            )
            stats["duration_s"] = round(monotonic() - t0, 2)
            await pipeline_runs.transition(session, run, STATUS_COMPLETED, stats=stats)
            return stats

        except Exception as exc:
            logger.exception("ranking pipeline failed run=%s", run_id)
            # Roll back the aborted transaction before attempting the FAILED
            # status write, otherwise postgres refuses the UPDATE with
            # InFailedSQLTransactionError.
            try:
                await session.rollback()
            except Exception:
                logger.exception("rollback after pipeline failure also failed")
            try:
                await pipeline_runs.transition(
                    session, run, STATUS_FAILED, error=str(exc)
                )
            except Exception:
                logger.exception("could not persist FAILED status for run=%s", run_id)
            raise
