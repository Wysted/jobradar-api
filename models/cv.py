from uuid import UUID

from pydantic import BaseModel, Field


class CVBatchRequest(BaseModel):
    min_score: int = Field(default=60, ge=0, le=100)
    limit: int = Field(default=100, ge=1, le=500)
    force: bool = False


class CVGenerateRequest(BaseModel):
    score_id: UUID


class CVGenerateResponse(BaseModel):
    cv_path: str
    cv_model: str
    duration_s: float
