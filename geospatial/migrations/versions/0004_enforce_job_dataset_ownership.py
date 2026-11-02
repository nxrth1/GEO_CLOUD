"""Enforce job/dataset owner consistency in PostgreSQL.

Revision ID: 0004_enforce_job_dataset_owner
Revises: 0003_resource_ownership
"""
from alembic import op


revision = "0004_enforce_job_dataset_owner"
down_revision = "0003_resource_ownership"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL requires a unique key covering the referenced (id, owner_id)
    # pair before a composite foreign key can enforce that the owner matches.
    op.create_unique_constraint(
        "uq_datasets_id_owner",
        "datasets",
        ["id", "owner_id"],
    )
    op.create_foreign_key(
        "fk_jobs_source_dataset_owner",
        "jobs",
        "datasets",
        ["source_dataset_id", "owner_id"],
        ["id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_jobs_secondary_dataset_owner",
        "jobs",
        "datasets",
        ["secondary_dataset_id", "owner_id"],
        ["id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_jobs_result_dataset_owner",
        "jobs",
        "datasets",
        ["result_dataset_id", "owner_id"],
        ["id", "owner_id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade():
    op.drop_constraint("fk_jobs_result_dataset_owner", "jobs", type_="foreignkey")
    op.drop_constraint(
        "fk_jobs_secondary_dataset_owner", "jobs", type_="foreignkey"
    )
    op.drop_constraint("fk_jobs_source_dataset_owner", "jobs", type_="foreignkey")
    op.drop_constraint("uq_datasets_id_owner", "datasets", type_="unique")
