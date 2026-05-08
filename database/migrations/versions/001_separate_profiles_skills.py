"""create platforms and jobs tables

Revision ID: 001_separate_profiles_skills
Revises:
Create Date: 2026-04-03

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID

# revision identifiers, used by Alembic.
revision = "001_separate_profiles_skills"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platforms",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.execute(
        sa.text(
            "INSERT INTO platforms (id, name, display_name, base_url) VALUES "
            "(gen_random_uuid(), 'linkedin', 'LinkedIn', 'https://www.linkedin.com'), "
            "(gen_random_uuid(), 'chiletrabajos', 'ChileTrabajos', 'https://www.chiletrabajos.cl'), "
            "(gen_random_uuid(), 'getonbrd', 'Get on Board', 'https://www.getonbrd.com')"
        )
    )

    op.create_table(
        "jobs",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "platform_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("platforms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(255), nullable=False, server_default=""),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("posted_date", sa.String(100), nullable=False, server_default=""),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", ARRAY(sa.Text()), nullable=True),
        sa.Column("scraped_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("platform_id", "external_id", name="uq_jobs_platform_id_external_id"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("platforms")
