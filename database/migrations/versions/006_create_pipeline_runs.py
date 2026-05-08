"""create pipeline_runs table

Revision ID: 006_create_pipeline_runs
Revises: 005_create_user_profiles
Create Date: 2026-04-17

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

# revision identifiers, used by Alembic.
revision = "006_create_pipeline_runs"
down_revision = "005_create_user_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('scrape', 'rank', 'backfill')",
            name="ck_pipeline_runs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_pipeline_runs_status",
        ),
    )

    # For "latest run of a kind" queries.
    op.create_index(
        "idx_pipeline_runs_kind_started",
        "pipeline_runs",
        ["kind", sa.text("started_at DESC")],
    )

    # Concurrency guard: at most one active run (queued OR running) per kind.
    # Relies on the unique partial index being atomic at the DB level.
    op.execute(
        "CREATE UNIQUE INDEX ux_pipeline_runs_active_per_kind "
        "ON pipeline_runs (kind) "
        "WHERE status IN ('queued', 'running');"
    )


def downgrade() -> None:
    op.drop_index("ux_pipeline_runs_active_per_kind", table_name="pipeline_runs")
    op.drop_index("idx_pipeline_runs_kind_started", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
