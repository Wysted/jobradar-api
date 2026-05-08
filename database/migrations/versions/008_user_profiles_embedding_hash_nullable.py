"""user_profiles: make embedding_hash nullable (spec R1: null on first create)

Revision ID: 008_user_profiles_hash_nullable
Revises: 007_create_job_scores
Create Date: 2026-04-19

"""

import sqlalchemy as sa
from alembic import op

revision = "008_user_profiles_hash_nullable"
down_revision = "007_create_job_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "user_profiles",
        "embedding_hash",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("UPDATE user_profiles SET embedding_hash = '' WHERE embedding_hash IS NULL")
    op.alter_column(
        "user_profiles",
        "embedding_hash",
        existing_type=sa.Text(),
        nullable=False,
    )
