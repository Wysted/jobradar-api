import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.database.pipeline_run import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    VALID_TRANSITIONS,
    PipelineRun,
)

logger = logging.getLogger(__name__)


class ActiveRunConflict(Exception):
    def __init__(self, existing_run_id) -> None:
        self.existing_run_id = existing_run_id
        super().__init__(f"active run already exists: {existing_run_id}")


class InvalidTransition(Exception):
    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"invalid transition {current!r} → {requested!r}; "
            f"allowed: {VALID_TRANSITIONS.get(current, set())}"
        )


async def create_run(
    session: AsyncSession, kind: str, trigger: str | None = None
) -> PipelineRun:
    run = PipelineRun(
        kind=kind,
        status="queued",
        started_at=datetime.now(timezone.utc),
        stats={},
    )
    if trigger is not None:
        # Stored for observability; no dedicated column yet, embed in stats.
        run.stats = {"trigger": trigger}
    session.add(run)
    try:
        await session.flush()
        await session.commit()
        await session.refresh(run)
        return run
    except IntegrityError:
        await session.rollback()
        result = await session.execute(
            select(PipelineRun).where(
                PipelineRun.kind == kind,
                PipelineRun.status.in_(["queued", "running"]),
            )
        )
        existing = result.scalar_one()
        raise ActiveRunConflict(existing.id)


async def get_run(session: AsyncSession, run_id) -> PipelineRun | None:
    result = await session.execute(
        select(PipelineRun).where(PipelineRun.id == run_id)
    )
    return result.scalar_one_or_none()


async def list_runs(
    session: AsyncSession, *, kind: str | None = None, limit: int = 50
) -> list[PipelineRun]:
    stmt = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
    if kind is not None:
        stmt = stmt.where(PipelineRun.kind == kind)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def transition(
    session: AsyncSession, run: PipelineRun, new_status: str, **fields
) -> PipelineRun:
    allowed = VALID_TRANSITIONS.get(run.status, set())
    if new_status not in allowed:
        raise InvalidTransition(run.status, new_status)

    run.status = new_status

    if new_status in (STATUS_COMPLETED, STATUS_FAILED):
        run.finished_at = datetime.now(timezone.utc)

    if "stats" in fields:
        run.stats = fields["stats"]
    if "error" in fields:
        run.error = fields["error"]

    await session.commit()
    await session.refresh(run)
    return run
