"""Create the jobs table.

Revision ID: 0001_create_jobs
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_create_jobs"
down_revision = None
branch_labels = None
depends_on = None

joboperation = postgresql.ENUM(
    "buffer",
    "clip",
    "difference",
    "dissolve",
    "spatial_join",
    "nearest_feature",
    name="joboperation",
    create_type=False,
)
jobstatus = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "failed",
    name="jobstatus",
    create_type=False,
)


def upgrade():
    bind = op.get_bind()
    joboperation.create(bind, checkfirst=True)
    jobstatus.create(bind, checkfirst=True)

    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("operation", joboperation, nullable=False),
        sa.Column(
            "source_dataset_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "secondary_dataset_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", jobstatus, nullable=False),
        sa.Column(
            "result_dataset_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jobs_status_created_at",
        "jobs",
        ["status", "created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
    jobstatus.drop(op.get_bind(), checkfirst=True)
    joboperation.drop(op.get_bind(), checkfirst=True)
