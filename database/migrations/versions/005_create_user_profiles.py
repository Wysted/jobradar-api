"""create user_profiles table

Revision ID: 005_create_user_profiles
Revises: 004_add_jobs_embedding_columns
Create Date: 2026-04-17

"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

# revision identifiers, used by Alembic.
revision = "005_create_user_profiles"
down_revision = "004_add_jobs_embedding_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Full semantic profile as submitted by the client.
        sa.Column("payload", JSONB, nullable=False),
        # sha256 of the canonical payload (embedding-affecting fields only).
        sa.Column("embedding_hash", sa.Text(), nullable=False),
        # The actual embedding vector (NULL until first embed run).
        sa.Column("embedding", Vector(1536), nullable=True),
        # Hash that corresponds to the stored embedding. Stale when != embedding_hash.
        sa.Column("embedded_hash", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Single-row guard for v1 single-user deployment.
    # CREATE UNIQUE INDEX ON (true) — every row evaluates to the same constant,
    # so only one row is allowed.
    op.execute(
        "CREATE UNIQUE INDEX ux_user_profiles_singleton "
        "ON user_profiles ((true));"
    )


def downgrade() -> None:
    op.drop_index("ux_user_profiles_singleton", table_name="user_profiles")
    op.drop_table("user_profiles")
