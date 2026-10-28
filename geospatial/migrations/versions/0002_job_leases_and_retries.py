"""Add worker leases and retry tracking to jobs.

Revision ID: 0002_job_leases_and_retries
Revises: 0001_create_jobs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_job_leases_and_retries"
down_revision = "0001_create_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "max_retries",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.create_index(
        "ix_jobs_status_expires_at",
        "jobs",
        ["status", "expires_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_jobs_status_expires_at", table_name="jobs")
    op.drop_column("jobs", "max_retries")
    op.drop_column("jobs", "retry_count")
    op.drop_column("jobs", "expires_at")
    op.drop_column("jobs", "worker_id")
