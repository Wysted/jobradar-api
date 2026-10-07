"""allow 'refresh' in pipeline_runs kind CHECK

Revision ID: 013_pipeline_runs_refresh_kind
Revises: 012_job_scores_status
Create Date: 2026-10-06

"""

from alembic import op

revision = "013_pipeline_runs_refresh_kind"
down_revision = "012_job_scores_status"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_pipeline_runs_kind"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "pipeline_runs", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "pipeline_runs",
        "kind IN ('scrape', 'rank', 'backfill', 'cv_batch', 'refresh')",
    )


def downgrade() -> None:
    # Rows with kind='refresh' would violate the old CHECK; remove them first.
    op.execute("DELETE FROM pipeline_runs WHERE kind = 'refresh'")
    op.drop_constraint(CONSTRAINT, "pipeline_runs", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "pipeline_runs",
        "kind IN ('scrape', 'rank', 'backfill', 'cv_batch')",
    )
