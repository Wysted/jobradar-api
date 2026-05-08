import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Full semantic profile as submitted by the client.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rut: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # sha256 of the canonical payload (embedding-affecting fields only).
    embedding_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The actual embedding vector (NULL until first embed run).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )
    # Hash that corresponds to the stored embedding. Stale when != embedding_hash.
    embedded_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
