import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_api_key
from models.database.job import JobRecord
from models.database.job_score import JOB_STATUS_DISCARDED, JOB_STATUS_NEW, JobScore
from models.ranking import (
    JobStatus,
    JobStatusIn,
    MatchedArea,
    MatchedTech,
    RankedJob,
    RankedJobsResponse,
)

router = APIRouter(prefix="/api/v1/jobs", tags=["ranked-jobs"])


def _ranked_job_out(score: JobScore, job: JobRecord) -> RankedJob:
    return RankedJob(
        job_id=str(score.job_id),
        profile_id=str(score.profile_id),
        vector_score=score.vector_score,
        llm_score=score.llm_score,
        matched_areas=[MatchedArea(**a) for a in score.matched_areas],
        matched_techs=[MatchedTech(**t) for t in score.matched_techs],
        missing_skills=score.missing_skills,
        red_flags=score.red_flags,
        pitch_angles=score.pitch_angles,
        reasoning=score.reasoning,
        llm_model=score.llm_model,
        scored_at=score.scored_at,
        viewed_at=score.viewed_at,
        status=score.status,
        status_changed_at=score.status_changed_at,
        title=job.title,
        company=job.company,
        location=job.location,
        url=job.url,
        platform=job.platform.name if job.platform else "",
        tags=job.tags or [],
    )


async def _get_score_or_404(db: AsyncSession, job_id: str) -> JobScore:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found",
        )
    result = await db.execute(select(JobScore).where(JobScore.job_id == job_uuid))
    score = result.scalar_one_or_none()
    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found",
        )
    return score


@router.get("/ranked", response_model=RankedJobsResponse, status_code=status.HTTP_200_OK)
async def get_ranked_jobs(
    only_new: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    min_score: int | None = Query(default=None, ge=0, le=100),
    status_filter: list[JobStatus] | None = Query(
        default=None,
        alias="status",
        description=(
            "Repeatable status filter (?status=new&status=interested). "
            "When omitted, 'discarded' jobs are excluded."
        ),
    ),
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> RankedJobsResponse:
    filters = []

    if only_new:
        filters.append(JobScore.viewed_at.is_(None))

    if min_score is not None:
        filters.append(JobScore.llm_score >= min_score)

    if status_filter:
        filters.append(JobScore.status.in_(status_filter))
    else:
        filters.append(JobScore.status != JOB_STATUS_DISCARDED)

    total = await db.scalar(
        select(func.count()).select_from(JobScore).where(*filters)
    )

    stmt = (
        select(JobScore, JobRecord)
        .join(JobRecord, JobScore.job_id == JobRecord.id)
        .where(*filters)
        .order_by(JobScore.llm_score.desc(), JobScore.scored_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()

    return RankedJobsResponse(
        total=total or 0,
        jobs=[_ranked_job_out(score, job) for score, job in rows],
    )


@router.put("/{job_id}/status", response_model=RankedJob, status_code=status.HTTP_200_OK)
async def set_job_status(
    job_id: str,
    body: JobStatusIn,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> RankedJob:
    score = await _get_score_or_404(db, job_id)
    now = datetime.now(timezone.utc)
    score.status = body.status
    score.status_changed_at = now
    if body.status != JOB_STATUS_NEW and score.viewed_at is None:
        score.viewed_at = now
    await db.commit()
    return _ranked_job_out(score, score.job)


@router.delete(
    "/{job_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def unmark_job_viewed(
    job_id: str,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> Response:
    score = await _get_score_or_404(db, job_id)
    score.viewed_at = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{job_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def mark_job_viewed(
    job_id: str,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found",
        )

    result = await db.execute(
        select(JobScore).where(JobScore.job_id == job_uuid)
    )
    score = result.scalar_one_or_none()

    if score is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="job not found",
        )

    score.viewed_at = datetime.now(timezone.utc)
    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
