import contextlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Job, JobStatus


LEASE_DURATION = timedelta(minutes=5)
HEARTBEAT_INTERVAL = 60
RETRY_BACKOFF_BASE = 10
RETRY_BACKOFF_MAX = 300

logger = logging.getLogger(__name__)


def _schedule_retry_or_fail(job: Job, now: datetime, message: str) -> None:
    job.error_message = message
    job.result_dataset_id = None
    job.worker_id = None

    if job.retry_count < job.max_retries:
        job.retry_count += 1
        job.status = JobStatus.QUEUED
        job.started_at = None
        job.finished_at = None
        backoff_seconds = min(
            RETRY_BACKOFF_BASE * 2 ** (job.retry_count - 1),
            RETRY_BACKOFF_MAX,
        )
        job.expires_at = now + timedelta(seconds=backoff_seconds)
    else:
        job.status = JobStatus.FAILED
        job.finished_at = now
        job.expires_at = None


def _mark_running(job: Job, worker_id: UUID, now: datetime) -> None:
    job.status = JobStatus.RUNNING
    job.worker_id = worker_id
    job.started_at = now
    job.expires_at = now + LEASE_DURATION
    job.error_message = None
    job.result_dataset_id = None
    job.finished_at = None


def _mark_succeeded(job: Job, result_dataset_id: UUID, now: datetime) -> None:
    job.status = JobStatus.SUCCEEDED
    job.result_dataset_id = result_dataset_id
    job.error_message = None
    job.worker_id = None
    job.expires_at = None
    job.finished_at = now


def claim_next_job(worker_id: UUID | None = None):
    worker_id = worker_id or uuid4()
    now = datetime.now(timezone.utc)

    with SessionLocal.begin() as session:
        reap_stale_jobs(session, now)
        job = session.execute(
            select(Job)
            .where(
                Job.status == JobStatus.QUEUED,
                or_(Job.expires_at.is_(None), Job.expires_at <= now),
            )
            .order_by(Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()

        if job is None:
            return None

        _mark_running(job, worker_id, now)
        return {
            "id": job.id,
            "owner_id": job.owner_id,
            "worker_id": worker_id,
            "operation": job.operation,
            "source_dataset_id": job.source_dataset_id,
            "secondary_dataset_id": job.secondary_dataset_id,
            "parameters": job.parameters,
        }


@contextlib.contextmanager
def heartbeat_context(session_factory, job_id: UUID, worker_id: UUID):
    stop_event = threading.Event()

    def heartbeat_loop():
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            try:
                with session_factory.begin() as session:
                    job = session.execute(
                        select(Job)
                        .where(
                            Job.id == job_id,
                            Job.worker_id == worker_id,
                            Job.status == JobStatus.RUNNING,
                        )
                        .with_for_update()
                    ).scalar_one_or_none()
                    if job is None:
                        return
                    job.expires_at = datetime.now(timezone.utc) + LEASE_DURATION
            except Exception:
                logger.exception("Could not renew lease for job %s.", job_id)

    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=2)


def reap_stale_jobs(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    stale_jobs = session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.RUNNING,
            or_(Job.expires_at.is_(None), Job.expires_at <= now),
        )
        .with_for_update(skip_locked=True)
    ).scalars().all()

    for job in stale_jobs:
        _schedule_retry_or_fail(job, now, "Job lease expired.")

    return len(stale_jobs)
