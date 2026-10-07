import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


JOB_STATUS_NEW = "new"
JOB_STATUS_INTERESTED = "interested"
JOB_STATUS_APPLIED = "applied"
JOB_STATUS_DISCARDED = "discarded"
VALID_JOB_STATUSES = {
    JOB_STATUS_NEW,
    JOB_STATUS_INTERESTED,
    JOB_STATUS_APPLIED,
    JOB_STATUS_DISCARDED,
}


class JobScore(Base):
    __tablename__ = "job_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # ux_job_scores_job — one score per job (v1)
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    vector_score: Mapped[float] = mapped_column(Float, nullable=False)
    llm_score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_areas: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    matched_techs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    missing_skills: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    red_flags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pitch_angles: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    # only_new mechanism (Q3): NULL = unviewed.
    viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=JOB_STATUS_NEW, server_default=JOB_STATUS_NEW
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cv_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cv_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cv_model: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["JobRecord"] = relationship(  # type: ignore[name-defined]
        "JobRecord", lazy="selectin"
    )
    profile: Mapped["UserProfile"] = relationship(  # type: ignore[name-defined]
        "UserProfile", lazy="selectin"
    )
