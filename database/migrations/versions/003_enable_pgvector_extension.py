"""enable pgvector extension

Revision ID: 003_enable_pgvector_extension
Revises: 002_add_bne_platform
Create Date: 2026-04-17

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "003_enable_pgvector_extension"
down_revision = "002_add_bne_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")


def downgrade() -> None:
    # Do NOT drop the extension on downgrade: other tables may depend on it.
    pass
