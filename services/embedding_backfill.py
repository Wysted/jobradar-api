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


class BackfillBatchError(Exception):
    """An embedding batch failed; carries the stats gathered so far."""

    def __init__(self, stats: dict, cause: Exception) -> None:
        self.stats = stats
        super().__init__(str(cause))


async def embed_pending(
    session,
    *,
    batch_size: int,
    max_jobs: int | None,
    stats: dict | None = None,
) -> dict:
    """Embed jobs with NULL embedding in batches. Raises BackfillBatchError on embed failure."""
    client = get_openai_client()
    if stats is None:
        stats = {}
    stats.update({"jobs_embedded": 0, "batches_run": 0, "errors": 0})
    total_processed = 0

    while True:
        remaining = None
        if max_jobs is not None:
            remaining = max_jobs - total_processed
            if remaining <= 0:
                break

        limit = batch_size if remaining is None else min(batch_size, remaining)

        stmt = select(JobRecord).where(JobRecord.embedding.is_(None)).limit(limit)
        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        if not jobs:
            break

        texts = [_build_job_text(j) for j in jobs]

        try:
            embeddings = await embed_texts(client, texts)
        except Exception as exc:
            logger.exception(
                "embed_texts failed batch starting job=%s: %s", jobs[0].id, exc
            )
            stats["errors"] += len(jobs)
            # Persistent failure would re-select the same rows forever: abort.
            raise BackfillBatchError(stats, exc) from exc

        now = datetime.now(timezone.utc)
        for job, embedding in zip(jobs, embeddings):
            job.embedding = embedding
            job.embedded_at = now
            job.embedding_model = EMBEDDING_MODEL

        await session.commit()

        stats["jobs_embedded"] += len(jobs)
        stats["batches_run"] += 1
        total_processed += len(jobs)

        # Fewer rows returned than requested: no more unembedded jobs.
        if len(jobs) < limit:
            break

    return stats


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

        stats = {"jobs_embedded": 0, "batches_run": 0, "errors": 0}

        try:
            await embed_pending(
                session, batch_size=batch_size, max_jobs=max_jobs, stats=stats
            )
            await pipeline_runs.transition(session, run, STATUS_COMPLETED, stats=stats)

        except BackfillBatchError as exc:
            await pipeline_runs.transition(
                session, run, STATUS_FAILED, stats=exc.stats, error=str(exc)
            )
            return

        except Exception as exc:
            logger.exception("backfill pipeline failed run=%s", run_id)
            await pipeline_runs.transition(
                session, run, STATUS_FAILED,
                stats=stats,
                error=str(exc),
            )
            raise
