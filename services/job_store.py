from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.common import Job
from models.database.job import JobRecord
from models.database.platform import Platform


async def _resolve_platform_id(db: AsyncSession, platform_name: str):
    result = await db.execute(select(Platform).where(Platform.name == platform_name))
    platform = result.scalar_one_or_none()
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Platform '{platform_name}' not found in database",
        )
    return platform.id


async def upsert_jobs(db: AsyncSession, jobs: list[Job], platform_name: str) -> int:
    platform_id = await _resolve_platform_id(db, platform_name)
    new_count = 0

    for job in jobs:
        result = await db.execute(
            select(JobRecord).where(
                JobRecord.platform_id == platform_id,
                JobRecord.external_id == job.external_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if job.description and job.description != existing.description:
                existing.description = job.description
        else:
            record = JobRecord(
                platform_id=platform_id,
                external_id=job.external_id,
                title=job.title,
                company=job.company,
                location=job.location,
                posted_date=job.posted_date,
                url=job.url,
                description=job.description,
                scraped_at=datetime.now(timezone.utc),
            )
            db.add(record)
            new_count += 1

    await db.commit()
    return new_count
