"""add triage status columns to job_scores

Revision ID: 012_job_scores_status
Revises: 011_pipeline_runs_cv_batch_kind
Create Date: 2026-10-06

"""

import sqlalchemy as sa
from alembic import op

revision = "012_job_scores_status"
down_revision = "011_pipeline_runs_cv_batch_kind"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_job_scores_status"


def upgrade() -> None:
    # server_default backfills existing rows to 'new'.
    op.add_column(
        "job_scores",
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
    )
    op.add_column(
        "job_scores",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        CONSTRAINT,
        "job_scores",
        "status IN ('new', 'interested', 'applied', 'discarded')",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "job_scores", type_="check")
    op.drop_column("job_scores", "status_changed_at")
    op.drop_column("job_scores", "status")
