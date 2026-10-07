import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

import config
from database import async_session
from models.database.pipeline_run import (
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    PipelineRun,
)
from models.database.profile import UserProfile
from routers import internal as internal_router
from routers.internal_cv import router as internal_cv_router
from routers.internal_scrape import router as internal_scrape_router
from routers.pipeline import router as pipeline_router
from routers.profile import router as profile_router
from routers.ranking import router as ranking_router
from routers.settings import router as settings_router
from services.cv_generator.adapter import SYSTEM_PROMPT as CV_SYSTEM_PROMPT
from services import pipeline_runs
from services.cv_generator.token_check import measure_prefix_tokens

logger = logging.getLogger(__name__)


async def _check_cv_prompt_cache() -> None:
    try:
        async with async_session() as session:
            result = await session.execute(select(UserProfile).limit(1))
            profile = result.scalar_one_or_none()
        if profile is None:
            logger.info("cv prompt cache check skipped: no user_profile rows")
            return
        payload = profile.payload or {}
        digest = payload.get("digest")
        if not digest:
            logger.info("cv prompt cache check skipped: profile has no digest yet")
            return
        digest_text = json.dumps(digest, ensure_ascii=False, indent=2)
        measure_prefix_tokens(config.OPENAI_CV_MODEL, CV_SYSTEM_PROMPT, digest_text)
    except Exception:
        logger.exception("cv prompt cache check failed (non-fatal)")


INTERRUPTED_RUN_ERROR = "interrupted by server restart"


async def _fail_orphaned_runs() -> None:
    """Fail queued/running runs left behind by a previous process.

    Safe only because runs execute as in-process BackgroundTasks on a single
    worker: at startup no live task can own a queued/running row.
    """
    try:
        async with async_session() as session:
            result = await session.execute(
                select(PipelineRun).where(
                    PipelineRun.status.in_([STATUS_QUEUED, STATUS_RUNNING])
                )
            )
            orphans = list(result.scalars().all())
            for run in orphans:
                await pipeline_runs.transition(
                    session, run, STATUS_FAILED, error=INTERRUPTED_RUN_ERROR
                )
        if orphans:
            logger.warning(
                "marked %d orphaned pipeline run(s) as failed on startup", len(orphans)
            )
    except Exception:
        logger.exception("orphaned run recovery failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _fail_orphaned_runs()
    await _check_cv_prompt_cache()
    yield


app = FastAPI(
    title="Scraping Masive API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(profile_router)
app.include_router(ranking_router)
app.include_router(pipeline_router)
app.include_router(settings_router)
app.include_router(internal_router.router)
app.include_router(internal_scrape_router)
app.include_router(internal_cv_router)
