import logging
import signal
from threading import Event
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from .database import SessionLocal
from .datasets import delete_dataset, load_dataset
from .dataset_repository import get_owned_dataset, save_owned_dataset
from .models import Dataset, Job, JobOperation, JobStatus
from .mechanics import (
    _mark_succeeded,
    _schedule_retry_or_fail,
    claim_next_job,
    heartbeat_context,
)
from .operations import (
    buffer,
    clip,
    difference,
    dissolve,
    nearest_feature,
    spatial_join,
)
from .processes import validate_json

from .logging_config import setup_logging

setup_logging(service_name="worker")
logger = logging.getLogger(__name__)


def _load_job_dataset(session, dataset_id, owner_id, field_name):
    """Load an input only when its persisted owner matches the job owner."""
    if dataset_id is None:
        raise ValueError(f"The job {field_name.lower()} dataset is unavailable.")

    try:
        dataset_uuid = (
            dataset_id if isinstance(dataset_id, UUID) else UUID(str(dataset_id))
        )
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(
            f"The job {field_name.lower()} dataset is unavailable."
        ) from error

    dataset_record = get_owned_dataset(session, dataset_uuid, owner_id)
    if dataset_record is None:
        # Deliberately avoid exposing whether an ID exists under another owner.
        raise ValueError(
            f"The job {field_name.lower()} dataset is unavailable to its owner."
        )

    try:
        backend = getattr(dataset_record, "storage_backend", None)
        storage_key = getattr(dataset_record, "storage_key", None)
        if backend and storage_key:
            return load_dataset(
                dataset_uuid, storage_backend=backend, storage_key=storage_key
            )
        return load_dataset(dataset_uuid)
    except (OSError, ValueError) as error:
        raise ValueError(
            f"The job {field_name.lower()} dataset could not be read."
        ) from error


def execute_job(job_data):
    owner_id = job_data.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 255:
        raise ValueError("Job has no valid persisted owner.")

    try:
        operation = JobOperation(job_data["operation"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Job has an unsupported operation.") from error

    parameters = job_data.get("parameters")
    if not isinstance(parameters, dict):
        raise TypeError("Job parameters must be an object.")

    # Validate ownership again at execution time. API checks alone are not enough
    # because records may be changed manually or by future internal code.
    with SessionLocal() as session:
        source = _load_job_dataset(
            session, job_data.get("source_dataset_id"), owner_id, "Source"
        )
        secondary_id = job_data.get("secondary_dataset_id")
        secondary = None
        if secondary_id is not None:
            secondary = _load_job_dataset(
                session, secondary_id, owner_id, "Secondary"
            )

    validation = validate_json(source)
    if not validation["accepted"]:
        raise ValueError(f"Source dataset failed validation: {validation['issues']}")

    def load_secondary_dataset(field_name):
        if secondary is None:
            raise ValueError(
                f"The {operation.value} operation requires a secondary dataset."
            )
        validation = validate_json(secondary)
        if not validation["accepted"]:
            raise ValueError(
                f"{field_name} dataset failed validation: {validation['issues']}"
            )
        return secondary

    if operation is JobOperation.BUFFER:
        return buffer(
            source,
            parameters["projected_crs"],
            parameters["distance_in_metres"],
        )
    if operation is JobOperation.CLIP:
        return clip(
            source,
            load_secondary_dataset("Mask"),
            parameters["analysis_crs"],
        )
    if operation is JobOperation.DIFFERENCE:
        return difference(
            source,
            load_secondary_dataset("Mask"),
            parameters["analysis_crs"],
        )
    if operation is JobOperation.DISSOLVE:
        return dissolve(
            source,
            parameters.get("by"),
            parameters["analysis_crs"],
        )
    if operation is JobOperation.SPATIAL_JOIN:
        return spatial_join(
            source,
            load_secondary_dataset("Mask"),
            parameters["how"],
            parameters["predicate"],
            parameters["analysis_crs"],
        )
    if operation is JobOperation.NEAREST_FEATURE:
        return nearest_feature(
            source,
            load_secondary_dataset("Target"),
            parameters["projected_crs"],
            parameters["how"],
            parameters["distance_col"],
        )

    raise ValueError(f"Unsupported operation: {operation.value}")

def save_job_result(result, owner_id):
    if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 255:
        raise ValueError("Cannot save a job result without a valid persisted owner.")
    with SessionLocal() as session:
        return save_owned_dataset(session, result, owner_id)


def _get_running_job(session, job_id, worker_id):
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
        raise ValueError(f"Worker no longer owns running job {job_id}.")
    return job


def mark_succeeded(job_id, worker_id, result_dataset_id):
    with SessionLocal.begin() as session:
        job = _get_running_job(session, job_id, worker_id)
        _mark_succeeded(job, result_dataset_id, datetime.now(timezone.utc))


def mark_failed(job_id, worker_id, message):
    with SessionLocal.begin() as session:
        job = _get_running_job(session, job_id, worker_id)
        _schedule_retry_or_fail(job, datetime.now(timezone.utc), str(message))


def process_next_job(worker_id: UUID | None = None):
    worker_id = worker_id or uuid4()
    job_data = claim_next_job(worker_id)
    if job_data is None:
        return False

    try:
        with heartbeat_context(SessionLocal, job_data["id"], worker_id):
            result = execute_job(job_data)
            result_dataset_id = save_job_result(result, job_data["owner_id"])
            mark_succeeded(job_data["id"], worker_id, result_dataset_id)
    except Exception as error:
        mark_failed(job_data["id"], worker_id, str(error))
        return True

    return True


def process_pending_dataset_cleanup(limit: int = 10) -> int:
    """Retry physical deletion for soft-deleted datasets whose cleanup is pending."""
    cleaned = 0
    for _ in range(max(1, limit)):
        try:
            with SessionLocal.begin() as session:
                record = session.execute(
                    select(Dataset)
                    .where(
                        Dataset.deleted_at.is_not(None),
                        Dataset.storage_deleted_at.is_(None),
                    )
                    .order_by(Dataset.deleted_at, Dataset.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                if record is None:
                    break
                # DatasetStorage.delete is defined to be idempotent, including when
                # an earlier attempt removed the object but failed to update the DB.
                delete_dataset(
                    record.id,
                    storage_backend=record.storage_backend,
                    storage_key=record.storage_key,
                )
                record.storage_deleted_at = datetime.now(timezone.utc)
            cleaned += 1
        except Exception:
            logger.exception("Dataset storage cleanup attempt failed; it will be retried.")
            break
    return cleaned


def run_worker(poll_interval=1.0):
    worker_id = uuid4()

    shutdown_event = Event()

    def handle_shutdown(signum, frame):
        logger.info(
            "Worker shutdown requested",
            extra={
                "event": "worker_shutdown_requested",
                "worker_id": str(worker_id),
                "signal": signum
            }
        )

        shutdown_event.set()

    # Listen for operating system shutdown signals
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    logger.info(
        "Worker started",
        extra={
            "event": "worker_started",
            "worker_id": str(worker_id)
        }
    )

    try:
        while not shutdown_event.is_set():

            did_work = process_next_job(worker_id)

            # Don't start additional work after shutdown is requested
            if shutdown_event.is_set():
                break

            process_pending_dataset_cleanup()

            if not did_work:
                shutdown_event.wait(poll_interval)

    finally:
        logger.info(
            "Worker stopped",
            extra={
                "event": "worker_stopped",
                "worker_id": str(worker_id)
            }
        )


if __name__ == "__main__":
    run_worker()


