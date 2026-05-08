import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("platform_id", "external_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    platform_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("platforms.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str] = mapped_column(String(255))
    # posted_date es String intencionalmente: los scrapers devuelven formatos variables
    posted_date: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Embedding columns added by migration 004.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    platform: Mapped["Platform"] = relationship("Platform", lazy="selectin")
