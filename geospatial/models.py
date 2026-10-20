import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, Index, String
from sqlalchemy.dialects.postgresql import UUID, JSONB
from .database import Base


def _enum_values(enum_class):
    return [member.value for member in enum_class]


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
    __table_args__ = (Index("ix_jobs_status_created_at", "status", "created_at"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    result_dataset_id = Column(UUID(as_uuid=True), nullable=True)
    error_message = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
