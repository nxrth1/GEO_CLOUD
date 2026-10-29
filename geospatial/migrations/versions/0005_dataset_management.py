"""Add dataset metadata, storage identifiers, and soft-delete lifecycle.

Revision ID: 0005_dataset_management
Revises: 0004_enforce_job_dataset_ownership
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_dataset_management"
down_revision = "0004_enforce_job_dataset_ownership"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("datasets", sa.Column("name", sa.String(length=255), nullable=True))
    op.add_column(
        "datasets", sa.Column("original_filename", sa.String(length=255), nullable=True)
    )
    op.add_column("datasets", sa.Column("format", sa.String(length=32), nullable=True))
    op.add_column(
        "datasets", sa.Column("content_type", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("storage_backend", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("storage_key", sa.String(length=512), nullable=True)
    )
    op.add_column("datasets", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("datasets", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("datasets", sa.Column("feature_count", sa.BigInteger(), nullable=True))
    op.add_column(
        "datasets", sa.Column("geometry_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("datasets", sa.Column("crs", sa.String(length=128), nullable=True))
    op.add_column(
        "datasets", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("storage_deleted_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Existing records keep their IDs/owners. The actual files are inventoried
    # and their content-derived metadata is populated by the backfill command.
    op.execute(
        """
        UPDATE datasets
        SET format = 'geojson',
            content_type = 'application/geo+json',
            storage_backend = 'local',
            storage_key = id::text || '.json',
            name = 'Dataset ' || substr(id::text, 1, 8)
        """
    )
    op.alter_column("datasets", "format", nullable=False, server_default=sa.text("'geojson'"))
    op.alter_column(
        "datasets", "content_type", nullable=False,
        server_default=sa.text("'application/geo+json'")
    )
    op.alter_column("datasets", "storage_backend", nullable=False, server_default=sa.text("'local'"))
    op.alter_column("datasets", "storage_key", nullable=False)

    op.create_unique_constraint(
        "uq_datasets_storage_backend_key", "datasets", ["storage_backend", "storage_key"]
    )
    op.create_index(
        "ix_datasets_owner_created", "datasets", ["owner_id", "created_at", "id"]
    )
    op.create_index(
        "ix_datasets_pending_storage_cleanup",
        "datasets",
        ["deleted_at", "storage_deleted_at"],
    )


def downgrade():
    op.drop_index("ix_datasets_pending_storage_cleanup", table_name="datasets")
    op.drop_index("ix_datasets_owner_created", table_name="datasets")
    op.drop_constraint("uq_datasets_storage_backend_key", "datasets", type_="unique")
    for column in (
        "storage_deleted_at", "deleted_at", "crs", "bbox", "geometry_types",
        "feature_count", "sha256", "size_bytes", "storage_key", "storage_backend",
        "content_type", "format", "original_filename", "name",
    ):
        op.drop_column("datasets", column)
