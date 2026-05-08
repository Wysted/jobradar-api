"""add bne platform

Revision ID: 002_add_bne_platform
Revises: 001_separate_profiles_skills
Create Date: 2026-04-03

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "002_add_bne_platform"
down_revision = "001_separate_profiles_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO platforms (id, name, display_name, base_url) VALUES "
            "(gen_random_uuid(), 'bne', 'Bolsa Nacional de Empleo', 'https://www.bne.cl')"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM platforms WHERE name = 'bne'"))
