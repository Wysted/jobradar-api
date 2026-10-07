from datetime import datetime

from pydantic import BaseModel


class TriggerResponse(BaseModel):
    """202 body returned when a pipeline run is successfully enqueued."""
    run_id: str
    kind: str
    status: str


class ConflictResponse(BaseModel):
    """409 body returned when a run of the same kind is already active."""
    active_run_id: str
    kind: str


class RunOut(BaseModel):
    """Full representation of a pipeline_run row."""
    id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stats: dict
    error: str | None


class RefreshConflictResponse(BaseModel):
    """409 body of the public refresh endpoint: some pipeline run is active."""
    detail: str
    active_run_id: str
    active_kind: str
