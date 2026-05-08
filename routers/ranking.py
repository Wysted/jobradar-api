import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_api_key
from models.database.job import JobRecord
from models.database.job_score import JobScore
from models.ranking import MatchedArea, MatchedTech, RankedJob, RankedJobsResponse

router = APIRouter(prefix="/api/v1/jobs", tags=["ranked-jobs"])


@router.get("/ranked", response_model=RankedJobsResponse, status_code=status.HTTP_200_OK)
async def get_ranked_jobs(
    only_new: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    min_score: int | None = Query(default=None, ge=0, le=100),
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db),
) -> RankedJobsResponse:
    stmt = (
        select(JobScore, JobRecord)
        .join(JobRecord, JobScore.job_id == JobRecord.id)
    )

    if only_new:
        stmt = stmt.where(JobScore.viewed_at.is_(None))

    if min_score is not None:
        stmt = stmt.where(JobScore.llm_score >= min_score)

    stmt = (
        stmt
        .order_by(JobScore.llm_score.desc(), JobScore.scored_at.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    jobs = [
        RankedJob(
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
            title=job.title,
            company=job.company,
            location=job.location,
            url=job.url,
            platform=job.platform.name if job.platform else "",
            tags=job.tags or [],
        )
        for score, job in rows
    ]

    return RankedJobsResponse(total=len(jobs), jobs=jobs)


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
