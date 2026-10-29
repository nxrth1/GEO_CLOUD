import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    BigInteger,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from .database import Base


def _enum_values(enum_class):
    return [member.value for member in enum_class]


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        Index("ix_datasets_owner_id", "owner_id"),
        Index("ix_datasets_owner_created", "owner_id", "created_at", "id"),
        Index("ix_datasets_pending_storage_cleanup", "deleted_at", "storage_deleted_at"),
        UniqueConstraint("id", "owner_id", name="uq_datasets_id_owner"),
        UniqueConstraint(
            "storage_backend", "storage_key", name="uq_datasets_storage_backend_key"
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(String(255), nullable=False)

    # Human-facing and format metadata. File locations are server-generated keys,
    # never user-supplied paths.
    name = Column(String(255), nullable=True)
    original_filename = Column(String(255), nullable=True)
    format = Column(String(32), nullable=False, default="geojson", server_default=text("'geojson'"))
    content_type = Column(
        String(128), nullable=False, default="application/geo+json",
        server_default=text("'application/geo+json'"),
    )

    # Storage is deliberately represented as backend + opaque key so a future
    # object-storage adapter can be introduced without changing API routes.
    storage_backend = Column(
        String(32), nullable=False, default="local", server_default=text("'local'")
    )
    storage_key = Column(String(512), nullable=False)

    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    feature_count = Column(BigInteger, nullable=True)
    geometry_types = Column(JSONB, nullable=True)
    bbox = Column(JSONB, nullable=True)
    crs = Column(String(128), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    # Set only after the storage adapter has confirmed physical removal.
    storage_deleted_at = Column(DateTime(timezone=True), nullable=True)


class JobOperation(str, enum.Enum):
    BUFFER = "buffer"
    CLIP = "clip"
    DIFFERENCE = "difference"
    DISSOLVE = "dissolve"
    SPATIAL_JOIN = "spatial_join"
    NEAREST_FEATURE = "nearest_feature"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_status_expires_at", "status", "expires_at"),
        ForeignKeyConstraint(
            ["source_dataset_id", "owner_id"],
            ["datasets.id", "datasets.owner_id"],
            name="fk_jobs_source_dataset_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["secondary_dataset_id", "owner_id"],
            ["datasets.id", "datasets.owner_id"],
            name="fk_jobs_secondary_dataset_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["result_dataset_id", "owner_id"],
            ["datasets.id", "datasets.owner_id"],
            name="fk_jobs_result_dataset_owner",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(String(255), nullable=False)
    operation = Column(
        Enum(JobOperation, values_callable=_enum_values, name="joboperation"),
        nullable=False,
    )
    source_dataset_id = Column(UUID(as_uuid=True), nullable=False)
    secondary_dataset_id = Column(UUID(as_uuid=True), nullable=True)
    parameters = Column(JSONB, nullable=False, default=dict)
    status = Column(
        Enum(
            JobStatus,
            values_callable=_enum_values,
            name="jobstatus",
        ),
        nullable=False,
        default=JobStatus.QUEUED,
    )

    worker_id = Column(UUID(as_uuid=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_retries = Column(
        Integer, nullable=False, default=3, server_default=text("3")
    )

    result_dataset_id = Column(UUID(as_uuid=True), nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
