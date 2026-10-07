import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies.auth import require_api_key
from models.database.pipeline_run import (
    KIND_BACKFILL,
    KIND_RANK,
    KIND_REFRESH,
    KIND_SCRAPE,
)
from models.pipeline_run import RefreshConflictResponse, RunOut, TriggerResponse
from services import pipeline_runs
from services.pipeline_runs import ActiveRunConflict
from services.refresh_pipeline import run_refresh

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])

# A refresh chains scrape + embed + rank, so it must not overlap with any of them.
REFRESH_BLOCKING_KINDS = (KIND_SCRAPE, KIND_BACKFILL, KIND_RANK, KIND_REFRESH)
ACTIVE_RUN_DETAIL = "a pipeline run is already active"


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


def _conflict(active_run_id, active_kind: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=RefreshConflictResponse(
            detail=ACTIVE_RUN_DETAIL,
            active_run_id=str(active_run_id),
            active_kind=active_kind,
        ).model_dump(),
    )


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    responses={status.HTTP_409_CONFLICT: {"model": RefreshConflictResponse}},
)
async def trigger_refresh(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
):
    active = await pipeline_runs.find_active_run(db, REFRESH_BLOCKING_KINDS)
    if active is not None:
        return _conflict(active.id, active.kind)

    try:
        run = await pipeline_runs.create_run(db, kind=KIND_REFRESH, trigger="frontend")
    except ActiveRunConflict as exc:
        return _conflict(exc.existing_run_id, KIND_REFRESH)

    background_tasks.add_task(run_refresh, run.id)

    return TriggerResponse(run_id=str(run.id), kind=KIND_REFRESH, status="queued")


@router.get("/runs/latest", response_model=RunOut)
async def get_latest_run(
    kind: Literal["refresh"] = KIND_REFRESH,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
):
    runs = await pipeline_runs.list_runs(db, kind=kind, limit=1)
    if not runs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no runs found")
    return _run_out(runs[0])


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
):
    try:
        run_uuid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    run = await pipeline_runs.get_run(db, run_uuid)
    if run is None or run.kind != KIND_REFRESH:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return _run_out(run)
