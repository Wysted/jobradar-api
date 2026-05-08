"""create job_scores table

Revision ID: 007_create_job_scores
Revises: 006_create_pipeline_runs
Create Date: 2026-04-17

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

# revision identifiers, used by Alembic.
revision = "007_create_job_scores"
down_revision = "006_create_pipeline_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_scores",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "job_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("user_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vector_score", sa.Float(), nullable=False),
        sa.Column("llm_score", sa.Integer(), nullable=False),
        sa.Column(
            "matched_areas",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "matched_techs",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_skills",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "red_flags",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "pitch_angles",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "reasoning",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("llm_model", sa.Text(), nullable=False),
        sa.Column(
            "scored_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # viewed_at NULL = not yet seen by client (only_new mechanism, Q3).
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
        # One score per job; re-ranking updates in place (UPSERT on job_id).
        sa.UniqueConstraint("job_id", name="ux_job_scores_job"),
        sa.CheckConstraint(
            "llm_score BETWEEN 0 AND 100",
            name="ck_job_scores_llm_score_range",
        ),
    )

    # Primary ordering index for GET /api/v1/jobs/ranked.
    op.create_index(
        "idx_job_scores_score_desc",
        "job_scores",
        [sa.text("llm_score DESC"), sa.text("scored_at DESC")],
    )

    # Partial index for only_new=true filter (viewed_at IS NULL rows only).
    op.execute(
        "CREATE INDEX idx_job_scores_viewed_null "
        "ON job_scores (job_id) "
        "WHERE viewed_at IS NULL;"
    )


def downgrade() -> None:
    op.drop_index("idx_job_scores_viewed_null", table_name="job_scores")
    op.drop_index("idx_job_scores_score_desc", table_name="job_scores")
    op.drop_table("job_scores")
