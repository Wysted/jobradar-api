"""add rut column to user_profiles (UNIQUE, nullable)

Revision ID: 010_add_rut_to_user_profiles
Revises: 009_add_cv_generated
Create Date: 2026-04-19

"""

import sqlalchemy as sa
from alembic import op

revision = "010_add_rut_to_user_profiles"
down_revision = "009_add_cv_generated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("rut", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_user_profiles_rut", "user_profiles", ["rut"]
    )
    op.execute(
        "COMMENT ON COLUMN user_profiles.rut IS 'Chilean RUT normalized: dddddddd-d'"
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_profiles_rut", "user_profiles", type_="unique")
    op.drop_column("user_profiles", "rut")
