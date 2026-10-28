"""Add dataset and job ownership.

Revision ID: 0003_resource_ownership
Revises: 0002_job_leases_and_retries
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_resource_ownership"
down_revision = "0002_job_leases_and_retries"
branch_labels = None
depends_on = None


LEGACY_OWNER_ID = "__legacy_unowned__"


def upgrade():
    op.create_table(
        "datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_datasets_owner_id",
        "datasets",
        ["owner_id"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO datasets (id, owner_id)
            SELECT dataset_id, :legacy_owner
            FROM (
                SELECT source_dataset_id AS dataset_id FROM jobs
                UNION
                SELECT secondary_dataset_id AS dataset_id FROM jobs
                UNION
                SELECT result_dataset_id AS dataset_id FROM jobs
            ) AS legacy_dataset_ids
            WHERE dataset_id IS NOT NULL
            """
        ).bindparams(legacy_owner=LEGACY_OWNER_ID)
    )
    op.add_column(
        "jobs",
        sa.Column(
            "owner_id",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_OWNER_ID}'"),
        ),
    )
    op.alter_column("jobs", "owner_id", server_default=None)


def downgrade():
    op.drop_column("jobs", "owner_id")
    op.drop_index("ix_datasets_owner_id", table_name="datasets")
    op.drop_table("datasets")
