import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_internal_key
from models.database.pipeline_run import (
    KIND_BACKFILL,
    KIND_RANK,
    KIND_REFRESH,
    KIND_SCRAPE,
)
from models.pipeline_run import ConflictResponse, RunOut, TriggerResponse
from services import pipeline_runs
from services.pipeline_runs import ActiveRunConflict
from services.ranking.pipeline import run_ranking
from services.scrape_orchestrator import run_scrape
from services.embedding_backfill import run_backfill

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


class RankRunRequest(BaseModel):
    only_new: bool = True
    skip_already_scored: bool = True


class ScrapeRunRequest(BaseModel):
    platforms: list[str] | None = None
    query: str | None = None
    max_pages: int = Field(default=1, ge=1, le=20)
    location: str | None = None


class BackfillRequest(BaseModel):
    batch_size: int = Field(default=100, ge=1, le=500)
    max_jobs: int | None = None


async def _refresh_conflict(db: AsyncSession) -> JSONResponse | None:
    """409 if a refresh run is active: it chains scrape + embed + rank."""
    active = await pipeline_runs.find_active_run(db, (KIND_REFRESH,))
    if active is None:
        return None
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=ConflictResponse(
            active_run_id=str(active.id),
            kind=KIND_REFRESH,
        ).model_dump(),
    )


def _run_out(run) -> RunOut:
    return RunOut(
        id=str(run.id),
        kind=run.kind,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stats=run.stats or {},
        error=run.error,
    )


@router.post(
    "/rank/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}},
)
async def trigger_ranking(
    body: RankRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    conflict = await _refresh_conflict(db)
    if conflict is not None:
        return conflict

    try:
        run = await pipeline_runs.create_run(db, kind=KIND_RANK, trigger="n8n")
    except ActiveRunConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ConflictResponse(
                active_run_id=str(exc.existing_run_id),
                kind=KIND_RANK,
            ).model_dump(),
        )

    background_tasks.add_task(
        run_ranking,
        run.id,
        only_new=body.only_new,
        skip_already_scored=body.skip_already_scored,
    )

    return TriggerResponse(run_id=str(run.id), kind=KIND_RANK, status="queued")


@router.post(
    "/scrape/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}},
)
async def trigger_scrape(
    body: ScrapeRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    conflict = await _refresh_conflict(db)
    if conflict is not None:
        return conflict

    try:
        run = await pipeline_runs.create_run(db, kind=KIND_SCRAPE, trigger="n8n")
    except ActiveRunConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ConflictResponse(
                active_run_id=str(exc.existing_run_id),
                kind=KIND_SCRAPE,
            ).model_dump(),
        )

    background_tasks.add_task(
        run_scrape, run.id, body.platforms, body.query, body.max_pages, body.location
    )

    return TriggerResponse(run_id=str(run.id), kind=KIND_SCRAPE, status="queued")


@router.post(
    "/embed/backfill",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}},
)
async def trigger_backfill(
    body: BackfillRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    conflict = await _refresh_conflict(db)
    if conflict is not None:
        return conflict

    try:
        run = await pipeline_runs.create_run(db, kind=KIND_BACKFILL, trigger="n8n")
    except ActiveRunConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ConflictResponse(
                active_run_id=str(exc.existing_run_id),
                kind=KIND_BACKFILL,
            ).model_dump(),
        )

    background_tasks.add_task(run_backfill, run.id, body.batch_size, body.max_jobs)

    return TriggerResponse(run_id=str(run.id), kind=KIND_BACKFILL, status="queued")


@router.get(
    "/runs/latest",
    response_model=RunOut,
    status_code=status.HTTP_200_OK,
)
async def get_latest_run(
    kind: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    runs = await pipeline_runs.list_runs(db, kind=kind, limit=1)
    if not runs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no runs found"
        )
    return _run_out(runs[0])


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    status_code=status.HTTP_200_OK,
)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    run = await pipeline_runs.get_run(db, run_uuid)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return _run_out(run)


@router.get(
    "/runs",
    response_model=list[RunOut],
    status_code=status.HTTP_200_OK,
)
async def list_runs(
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    runs = await pipeline_runs.list_runs(db, kind=kind, limit=limit)
    return [_run_out(r) for r in runs]
