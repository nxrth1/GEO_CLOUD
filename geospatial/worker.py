import time
from datetime import datetime, timezone

from sqlalchemy import select

from .database import SessionLocal
from .datasets import load_dataset, save_dataset
from .models import Job, JobOperation, JobStatus
from .operations import (
    buffer,
    clip,
    difference,
    dissolve,
    nearest_feature,
    spatial_join,
)
from .processes import validate_json


def claim_next_job():
    with SessionLocal.begin() as session:
        job = session.execute(
            select(Job)
            .where(Job.status == JobStatus.QUEUED)
            .order_by(Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()

        if job is None:
            return None

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        job_data = {
            "id": job.id,
            "operation": job.operation,
            "source_dataset_id": job.source_dataset_id,
            "secondary_dataset_id": job.secondary_dataset_id,
            "parameters": job.parameters,
        }

    return job_data


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

def save_job_result(result):
    return save_dataset(result)


def _get_running_job(session, job_id):
    job = session.execute(
        select(Job).where(Job.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise LookupError(f"Job {job_id} was not found.")
    if job.status != JobStatus.RUNNING:
        raise ValueError(f"Job {job_id} is not running.")
    return job


def mark_succeeded(job_id, result_dataset_id):
    with SessionLocal.begin() as session:
        job = _get_running_job(session, job_id)
        job.status = JobStatus.SUCCEEDED
        job.result_dataset_id = result_dataset_id
        job.error_message = None
        job.finished_at = datetime.now(timezone.utc)


def mark_failed(job_id, message):
    with SessionLocal.begin() as session:
        job = _get_running_job(session, job_id)
        job.status = JobStatus.FAILED
        job.result_dataset_id = None
        job.error_message = str(message)
        job.finished_at = datetime.now(timezone.utc)


def process_next_job():
    job_data = claim_next_job()
    if job_data is None:
        return False

    try:
        result = execute_job(job_data)
        result_dataset_id = save_job_result(result)
    except Exception as error:
        mark_failed(job_data["id"], str(error))
        return True

    mark_succeeded(job_data["id"], result_dataset_id)
    return True


def run_worker(poll_interval=1.0):
    while True:
        if not process_next_job():
            time.sleep(poll_interval)


if __name__ == "__main__":
    run_worker()