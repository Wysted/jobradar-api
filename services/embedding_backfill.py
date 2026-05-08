import logging
from datetime import datetime, timezone

from sqlalchemy import select

from database import async_session
from dependencies.openai_client import EMBEDDING_MODEL, get_openai_client
from models.database.job import JobRecord
from models.database.pipeline_run import STATUS_COMPLETED, STATUS_FAILED, STATUS_RUNNING
from services import pipeline_runs
from services.embedding import embed_texts

logger = logging.getLogger(__name__)


def _build_job_text(job: JobRecord) -> str:
    parts = [
        job.title or "",
        job.company or "",
        job.location or "",
        job.description or "",
    ]
    return " | ".join(p for p in parts if p)


async def run_backfill(
    run_id,
    batch_size: int,
    max_jobs: int | None,
) -> None:
    async with async_session() as session:
        run = await pipeline_runs.get_run(session, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run {run_id} not found")

        await pipeline_runs.transition(session, run, STATUS_RUNNING)

        client = get_openai_client()
        stats = {"jobs_embedded": 0, "batches_run": 0, "errors": 0}

        try:
            total_processed = 0

            while True:
                remaining = None
                if max_jobs is not None:
                    remaining = max_jobs - total_processed
                    if remaining <= 0:
                        break

                limit = batch_size if remaining is None else min(batch_size, remaining)

                stmt = (
                    select(JobRecord)
                    .where(JobRecord.embedding.is_(None))
                    .limit(limit)
                )
                result = await session.execute(stmt)
                jobs = list(result.scalars().all())

                if not jobs:
                    break

                texts = [_build_job_text(j) for j in jobs]

                try:
                    embeddings = await embed_texts(client, texts)
                except Exception as exc:
                    logger.exception(
                        "embed_texts failed batch starting job=%s: %s",
                        jobs[0].id,
                        exc,
                    )
                    stats["errors"] += len(jobs)
                    # Skip this batch; next iteration will re-select the same rows
                    # (embedding still NULL). To avoid an infinite loop on persistent
                    # failure, break out and mark the run failed.
                    await pipeline_runs.transition(
                        session, run, STATUS_FAILED,
                        stats=stats,
                        error=str(exc),
                    )
                    return

                now = datetime.now(timezone.utc)
                for job, embedding in zip(jobs, embeddings):
                    job.embedding = embedding
                    job.embedded_at = now
                    job.embedding_model = EMBEDDING_MODEL

                await session.commit()

                stats["jobs_embedded"] += len(jobs)
                stats["batches_run"] += 1
                total_processed += len(jobs)

                # Fewer rows returned than requested — no more unembedded jobs.
                if len(jobs) < limit:
                    break

            await pipeline_runs.transition(session, run, STATUS_COMPLETED, stats=stats)

        except Exception as exc:
            logger.exception("backfill pipeline failed run=%s", run_id)
            await pipeline_runs.transition(
                session, run, STATUS_FAILED,
                stats=stats,
                error=str(exc),
            )
            raise
