import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from .database import SessionLocal
from .datasets import load_dataset
from .dataset_repository import save_owned_dataset
from .models import Job, JobOperation, JobStatus
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


def execute_job(job_data):
    source = load_dataset(job_data["source_dataset_id"])
    validation = validate_json(source)
    if not validation["accepted"]:
        raise ValueError(f"Source dataset failed validation: {validation['issues']}")

    try:
        operation = JobOperation(job_data["operation"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Job has an unsupported operation.") from error

    parameters = job_data.get("parameters")
    if not isinstance(parameters, dict):
        raise TypeError("Job parameters must be an object.")

    def load_secondary_dataset(field_name):
        secondary_dataset_id = job_data.get("secondary_dataset_id")
        if secondary_dataset_id is None:
            raise ValueError(f"The {operation.value} operation requires a secondary dataset.")

        secondary = load_dataset(secondary_dataset_id)
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


def run_worker(poll_interval=1.0):
    worker_id = uuid4()
    while True:
        if not process_next_job(worker_id):
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()


"""
Validate jobs before accepting them. Check that each operation has the required parameters and any required secondary dataset before creating a job. That way, bad jobs return a useful 422 immediately instead of failing later in the worker.
Add dataset ownership and access control. The current dataset IDs are not tied to users or tenants. Before making the API available beyond trusted local use, add authentication and ensure users can only access their own datasets and jobs.
Improve dataset management. Add listing and deletion, record dataset metadata, and define how uploads, size limits, and storage work. The current implementation stores GeoJSON files locally; a storage interface would make a later move to object storage easier.
Add basic operational support. Provide health/readiness checks, configurable CORS and settings, structured logs, and deployment setup for running the API and worker together.
Expand tests around the service lifecycle. Prioritize job recovery, dataset persistence, authorization boundaries, invalid job requests, and API error responses.
Best first step: implement job parameter validation and worker recovery together, with tests. Those address the most immediate gaps in the existing job-processing flow.

"""