from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .models import Job, JobOperation, JobStatus


def create_queued_job(
    session: Session,
    *,
    operation: JobOperation,
    owner_id: str,
    source_dataset_id: UUID,
    parameters: dict[str, Any],
    secondary_dataset_id: UUID | None = None,
) -> Job:
    job = Job(
        owner_id=owner_id,
        operation=operation,
        source_dataset_id=source_dataset_id,
        secondary_dataset_id=secondary_dataset_id,
        parameters=parameters,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: UUID, owner_id: str) -> Job | None:
    return session.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
    ).scalar_one_or_none()
