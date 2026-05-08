"""add cv generation tracking columns to job_scores

Revision ID: 009_add_cv_generated
Revises: 008_user_profiles_embedding_hash_nullable
Create Date: 2026-04-19

Adds nullable columns to track CV generation per ranked job:
- cv_generated_at: timestamp when the CV was produced (NULL = pending)
- cv_path: filesystem path to the generated PDF (NULL = not generated)
- cv_model: LLM model used for the adaptation (audit)

Includes a partial index on pending CVs to speed up batch selection.
"""

import sqlalchemy as sa
from alembic import op

revision = "009_add_cv_generated"
down_revision = "008_user_profiles_hash_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_scores",
        sa.Column("cv_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_scores",
        sa.Column("cv_path", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_scores",
        sa.Column("cv_model", sa.Text(), nullable=True),
    )

    op.execute(
        "CREATE INDEX idx_job_scores_cv_pending "
        "ON job_scores (llm_score DESC) "
        "WHERE cv_generated_at IS NULL;"
    )


def downgrade() -> None:
    op.drop_index("idx_job_scores_cv_pending", table_name="job_scores")
    op.drop_column("job_scores", "cv_model")
    op.drop_column("job_scores", "cv_path")
    op.drop_column("job_scores", "cv_generated_at");
