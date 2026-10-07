"""allow 'cv_batch' in pipeline_runs kind CHECK

Revision ID: 011_pipeline_runs_cv_batch_kind
Revises: 010_add_rut_to_user_profiles
Create Date: 2026-10-06

"""

from alembic import op

revision = "011_pipeline_runs_cv_batch_kind"
down_revision = "010_add_rut_to_user_profiles"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_pipeline_runs_kind"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "pipeline_runs", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "pipeline_runs",
        "kind IN ('scrape', 'rank', 'backfill', 'cv_batch')",
    )


def downgrade() -> None:
    # Rows with kind='cv_batch' would violate the old CHECK; remove them first.
    op.execute("DELETE FROM pipeline_runs WHERE kind = 'cv_batch'")
    op.drop_constraint(CONSTRAINT, "pipeline_runs", type_="check")
    op.create_check_constraint(
        CONSTRAINT,
        "pipeline_runs",
        "kind IN ('scrape', 'rank', 'backfill')",
    )
