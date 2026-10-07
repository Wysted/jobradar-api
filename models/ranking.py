from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LLM output schema (matches design §9 RERANK_RESPONSE_FORMAT)
# ---------------------------------------------------------------------------

class MatchedArea(BaseModel):
    name: str
    profile_sen: int = Field(..., ge=1, le=5)
    job_sen: int = Field(..., ge=1, le=5)


class MatchedTech(BaseModel):
    name: str
    profile_sen: int = Field(..., ge=1, le=5)
    job_sen: int = Field(..., ge=1, le=5)


class LLMScoreOutput(BaseModel):
    score_0_100: int = Field(..., ge=0, le=100)
    matched_areas: list[MatchedArea]
    matched_techs: list[MatchedTech]
    missing_skills: list[str]
    red_flags: list[str]
    pitch_angles: list[str]
    reasoning: str


# ---------------------------------------------------------------------------
# API response shapes
# ---------------------------------------------------------------------------

JobStatus = Literal["new", "interested", "applied", "discarded"]


class JobStatusIn(BaseModel):
    status: JobStatus


class RankedJob(BaseModel):
    """One entry in the ranked jobs list."""
    job_id: str
    profile_id: str
    vector_score: float
    llm_score: int = Field(..., ge=0, le=100)
    matched_areas: list[MatchedArea]
    matched_techs: list[MatchedTech]
    missing_skills: list[str]
    red_flags: list[str]
    pitch_angles: list[str]
    reasoning: str
    llm_model: str
    scored_at: datetime
    viewed_at: datetime | None
    status: JobStatus
    status_changed_at: datetime | None
    # Denormalized job fields for display without a second request.
    title: str
    company: str
    location: str
    url: str
    platform: str
    tags: list[str]


class RankedJobsResponse(BaseModel):
    total: int
    jobs: list[RankedJob]
