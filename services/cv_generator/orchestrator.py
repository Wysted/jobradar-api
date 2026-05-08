import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import async_session
from dependencies.openai_client import get_openai_client
from models.database.job import JobRecord
from models.database.job_score import JobScore
from models.database.pipeline_run import (
    KIND_CV_BATCH,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
)
from models.database.profile import UserProfile
from services import pipeline_runs
from services.cv_generator.adapter import adapt_profile_to_job
from services.cv_generator.digester import persist_digest, refresh_digest
from services.cv_generator.filename import build_filename
from services.cv_generator.renderer import generate_pdf

logger = logging.getLogger(__name__)

STATS_FLUSH_EVERY = 5


def _cv_output_dir() -> Path:
    return Path(getattr(config, "CV_OUTPUT_DIR", Path("/app/output/cv")))


def _concurrency() -> int:
    return int(getattr(config, "CV_BATCH_CONCURRENCY", 5))


def _empty_stats(min_score: int) -> dict:
    return {
        "min_score": min_score,
        "total_pending": 0,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped_duplicate": 0,
        "llm_tokens_in": 0,
        "llm_tokens_out": 0,
        "duration_s": 0.0,
    }


def _build_header(profile: UserProfile) -> dict:
    payload = profile.payload or {}
    info = payload.get("personal_info") or {}
    links = payload.get("links") or {}
    location = payload.get("location") or {}

    loc_parts = [p for p in [location.get("city"), location.get("region")] if p]
    location_str = ", ".join(loc_parts) or location.get("country", "")

    link_list = []
    if links.get("linkedin"):
        link_list.append({"label": "LinkedIn", "url": links["linkedin"]})
    if links.get("github"):
        link_list.append({"label": "GitHub", "url": links["github"]})
    if links.get("portfolio"):
        link_list.append({"label": "Portafolio", "url": links["portfolio"]})

    return {
        "name": info.get("name", ""),
        "location": location_str,
        "phone": info.get("phone", ""),
        "email": info.get("email", ""),
        "links": link_list,
    }


def _job_payload(job: JobRecord) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "posted_date": getattr(job, "posted_date", None),
        "url": getattr(job, "url", None),
        "description": job.description or "",
    }


def _score_payload(score: JobScore) -> dict:
    return {
        "llm_score": score.llm_score,
        "matched_areas": score.matched_areas or [],
        "matched_techs": score.matched_techs or [],
        "missing_skills": score.missing_skills or [],
        "pitch_angles": score.pitch_angles or [],
        "reasoning": score.reasoning or "",
    }


async def _load_profile(session: AsyncSession) -> UserProfile:
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise RuntimeError("no user_profiles row exists")
    return profile


async def _ensure_digest(session: AsyncSession, client, profile: UserProfile) -> dict:
    payload = profile.payload or {}
    digest = payload.get("digest")
    if digest:
        return digest
    logger.info("digest missing on profile %s — regenerating", profile.id)
    digest = await refresh_digest(client, payload)
    await persist_digest(session, profile, digest)
    return digest


async def _mark_generated(
    session: AsyncSession, score_id: uuid.UUID, cv_path: str, model: str
) -> None:
    await session.execute(
        update(JobScore)
        .where(JobScore.id == score_id)
        .values(
            cv_generated_at=datetime.now(timezone.utc),
            cv_path=cv_path,
            cv_model=model,
        )
    )
    await session.commit()


async def _fetch_pending_ids(
    session: AsyncSession, min_score: int, limit: int, force: bool
) -> list[uuid.UUID]:
    stmt = (
        select(JobScore.id)
        .where(JobScore.llm_score >= min_score)
        .order_by(JobScore.llm_score.desc())
        .limit(limit)
    )
    if not force:
        stmt = stmt.where(JobScore.cv_generated_at.is_(None))
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def _process_score(
    score_id: uuid.UUID, semaphore: asyncio.Semaphore, stats: dict
) -> None:
    async with semaphore:
        try:
            await generate_one(score_id)
            stats["succeeded"] += 1
        except Exception as exc:
            logger.exception("cv worker failed score_id=%s: %s", score_id, exc)
            stats["failed"] += 1
        finally:
            stats["processed"] += 1


async def generate_one(
    score_id: uuid.UUID, session: AsyncSession | None = None
) -> Path:
    own_session = session is None
    session = session or async_session()

    try:
        if own_session:
            session = await session.__aenter__()

        score_result = await session.execute(
            select(JobScore).where(JobScore.id == score_id)
        )
        score = score_result.scalar_one_or_none()
        if score is None:
            raise ValueError(f"job_score {score_id} not found")

        job = score.job
        profile = await _load_profile(session)

        client = get_openai_client()
        digest = await _ensure_digest(session, client, profile)

        content = await adapt_profile_to_job(
            client, digest, _job_payload(job), _score_payload(score)
        )

        header = _build_header(profile)
        filename = build_filename(profile, job)
        output_path = _cv_output_dir() / filename

        pdf_path = generate_pdf(header, content, output_path)

        await _mark_generated(
            session, score.id, str(pdf_path), config.OPENAI_CV_MODEL
        )
        return pdf_path
    finally:
        if own_session:
            await session.__aexit__(None, None, None)


async def run_cv_batch(
    run_id: uuid.UUID,
    min_score: int,
    limit: int,
    force: bool,
) -> dict:
    async with async_session() as session:
        run = await pipeline_runs.get_run(session, run_id)
        if run is None:
            raise RuntimeError(f"pipeline run {run_id} not found")
        if run.kind != KIND_CV_BATCH:
            raise RuntimeError(f"run {run_id} has kind {run.kind}, expected {KIND_CV_BATCH}")

        await pipeline_runs.transition(session, run, STATUS_RUNNING)
        t0 = monotonic()
        stats = _empty_stats(min_score)

        try:
            pending_ids = await _fetch_pending_ids(session, min_score, limit, force)
            stats["total_pending"] = len(pending_ids)
            run.stats = dict(stats)
            await session.commit()

            if not pending_ids:
                stats["duration_s"] = round(monotonic() - t0, 2)
                await pipeline_runs.transition(
                    session, run, STATUS_COMPLETED, stats=stats
                )
                return stats

            semaphore = asyncio.Semaphore(_concurrency())
            tasks = [
                asyncio.create_task(_process_score(sid, semaphore, stats))
                for sid in pending_ids
            ]

            flushed = 0
            while tasks:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                tasks = list(pending)
                flushed += len(done)
                if flushed >= STATS_FLUSH_EVERY or not tasks:
                    flushed = 0
                    run.stats = dict(stats)
                    await session.commit()
                    await session.refresh(run)

            stats["duration_s"] = round(monotonic() - t0, 2)
            await pipeline_runs.transition(
                session, run, STATUS_COMPLETED, stats=stats
            )
            return stats

        except Exception as exc:
            logger.exception("cv batch failed run=%s", run_id)
            try:
                await session.rollback()
            except Exception:
                logger.exception("rollback after cv batch failure also failed")
            try:
                await pipeline_runs.transition(
                    session, run, STATUS_FAILED, error=str(exc)
                )
            except Exception:
                logger.exception("could not persist FAILED status for run=%s", run_id)
            raise
