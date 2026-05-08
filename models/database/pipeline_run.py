import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# ---------------------------------------------------------------------------
# Kind constants
# ---------------------------------------------------------------------------
KIND_SCRAPE = "scrape"
KIND_RANK = "rank"
KIND_BACKFILL = "backfill"
KIND_CV_BATCH = "cv_batch"
VALID_KINDS = {KIND_SCRAPE, KIND_RANK, KIND_BACKFILL, KIND_CV_BATCH}

# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
VALID_STATUSES = {STATUS_QUEUED, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED}

# Valid state transitions: from → allowed set of next states.
VALID_TRANSITIONS: dict[str, set[str]] = {
    STATUS_QUEUED: {STATUS_RUNNING, STATUS_FAILED},
    STATUS_RUNNING: {STATUS_COMPLETED, STATUS_FAILED},
    STATUS_COMPLETED: set(),
    STATUS_FAILED: set(),
}


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stats: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
