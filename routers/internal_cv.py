from time import monotonic

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import get_db
from dependencies.auth import require_internal_key
from models.cv import CVBatchRequest, CVGenerateRequest, CVGenerateResponse
from models.database.pipeline_run import KIND_CV_BATCH
from models.pipeline_run import ConflictResponse, TriggerResponse
from services import pipeline_runs
from services.cv_generator.orchestrator import generate_one, run_cv_batch
from services.pipeline_runs import ActiveRunConflict

router = APIRouter(
    prefix="/internal",
    tags=["internal-cv"],
    dependencies=[Depends(require_internal_key)],
)


@router.post(
    "/cv/batch-run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ConflictResponse}},
)
async def trigger_cv_batch(
    body: CVBatchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        run = await pipeline_runs.create_run(db, kind=KIND_CV_BATCH, trigger="n8n")
    except ActiveRunConflict as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ConflictResponse(
                active_run_id=str(exc.existing_run_id),
                kind=KIND_CV_BATCH,
            ).model_dump(),
        )

    background_tasks.add_task(
        run_cv_batch,
        run.id,
        body.min_score,
        body.limit,
        body.force,
    )

    return TriggerResponse(run_id=str(run.id), kind=KIND_CV_BATCH, status="queued")


@router.post(
    "/cv/generate",
    status_code=status.HTTP_200_OK,
    response_model=CVGenerateResponse,
)
async def generate_cv_single(body: CVGenerateRequest):
    t0 = monotonic()
    try:
        cv_path = await generate_one(body.score_id)
    except ValueError as exc:
        if "not found" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    return CVGenerateResponse(
        cv_path=str(cv_path),
        cv_model=config.OPENAI_CV_MODEL,
        duration_s=round(monotonic() - t0, 2),
    )
