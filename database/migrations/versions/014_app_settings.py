"""create app_settings key/value table

Revision ID: 014_app_settings
Revises: 013_pipeline_runs_refresh_kind
Create Date: 2026-10-07

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision = "014_app_settings"
down_revision = "013_pipeline_runs_refresh_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.Text(), nullable=False, unique=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
