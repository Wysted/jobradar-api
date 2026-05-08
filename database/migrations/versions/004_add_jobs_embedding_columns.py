"""add embedding columns to jobs

Revision ID: 004_add_jobs_embedding_columns
Revises: 003_enable_pgvector_extension
Create Date: 2026-04-17

"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "004_add_jobs_embedding_columns"
down_revision = "003_enable_pgvector_extension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Defensive: ensure pgvector is present before adding column type.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.add_column("jobs", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("embedding_model", sa.String(100), nullable=True),
    )

    # HNSW index for cosine similarity (ANN search, L2 in pgvector uses inner-product
    # for cosine via normalized vectors; vector_cosine_ops handles it directly).
    op.execute(
        "CREATE INDEX idx_jobs_embedding_hnsw ON jobs "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64);"
    )

    # Partial index to find un-embedded jobs efficiently.
    op.create_index(
        "idx_jobs_embedded_at_null",
        "jobs",
        ["embedded_at"],
        postgresql_where=sa.text("embedded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_jobs_embedded_at_null", table_name="jobs")
    op.drop_index("idx_jobs_embedding_hnsw", table_name="jobs")
    op.drop_column("jobs", "embedding_model")
    op.drop_column("jobs", "embedded_at")
    op.drop_column("jobs", "embedding")
