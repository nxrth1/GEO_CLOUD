from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import geospatial.mechanics as mechanics
import geospatial.worker as worker
from geospatial.mechanics import (
    RETRY_BACKOFF_BASE,
    _mark_running,
    _mark_succeeded,
    _schedule_retry_or_fail,
    claim_next_job,
    reap_stale_jobs,
)
from geospatial.models import JobStatus


def job(**overrides):
    values = {
        "owner_id": "test-owner",
        "status": JobStatus.QUEUED,
        "worker_id": None,
        "started_at": None,
        "expires_at": None,
        "finished_at": None,
        "retry_count": 0,
        "max_retries": 3,
        "result_dataset_id": None,
        "error_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_queued_job_runs_and_succeeds():
    current_time = datetime.now(timezone.utc)
    worker_id = uuid4()
    result_dataset_id = uuid4()
    queued_job = job()

    _mark_running(queued_job, worker_id, current_time)

    assert queued_job.status is JobStatus.RUNNING
    assert queued_job.worker_id == worker_id
    assert queued_job.started_at == current_time
    assert queued_job.expires_at > current_time

    _mark_succeeded(queued_job, result_dataset_id, current_time)

    assert queued_job.status is JobStatus.SUCCEEDED
    assert queued_job.result_dataset_id == result_dataset_id
    assert queued_job.worker_id is None
    assert queued_job.expires_at is None
    assert queued_job.finished_at == current_time
    assert queued_job.error_message is None


def test_claim_moves_queued_job_to_running(monkeypatch):
    queued_job = job(
        id=uuid4(),
        operation="buffer",
        source_dataset_id=uuid4(),
        secondary_dataset_id=None,
        parameters={"distance_in_metres": 10},
        created_at=datetime.now(timezone.utc),
    )
    fake_session = SimpleNamespace(execute=None)
    calls = 0

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class QueuedResult:
        def scalar_one_or_none(self):
            return queued_job

    def execute(statement):
        nonlocal calls
        calls += 1
        return EmptyResult() if calls == 1 else QueuedResult()

    fake_session.execute = execute

    class SessionFactory:
        @staticmethod
        @contextmanager
        def begin():
            yield fake_session

    monkeypatch.setattr(mechanics, "SessionLocal", SessionFactory)
    worker_id = uuid4()

    claimed_job = claim_next_job(worker_id)

    assert claimed_job["id"] == queued_job.id
    assert claimed_job["worker_id"] == worker_id
    assert queued_job.status is JobStatus.RUNNING
    assert queued_job.worker_id == worker_id


def test_failure_retries_with_backoff_then_fails_after_retry_limit():
    current_time = datetime.now(timezone.utc)
    failed_job = job(max_retries=2)

    _mark_running(failed_job, uuid4(), current_time)
    _schedule_retry_or_fail(failed_job, current_time, "temporary failure")

    assert failed_job.status is JobStatus.QUEUED
    assert failed_job.retry_count == 1
    assert failed_job.expires_at == current_time + timedelta(
        seconds=RETRY_BACKOFF_BASE
    )
    assert failed_job.finished_at is None
    assert failed_job.error_message == "temporary failure"

    retry_time = failed_job.expires_at
    _mark_running(failed_job, uuid4(), retry_time)
    _schedule_retry_or_fail(failed_job, retry_time, "temporary failure")

    assert failed_job.status is JobStatus.QUEUED
    assert failed_job.retry_count == 2
    assert failed_job.expires_at == retry_time + timedelta(
        seconds=RETRY_BACKOFF_BASE * 2
    )

    final_attempt_time = failed_job.expires_at
    _mark_running(failed_job, uuid4(), final_attempt_time)
    _schedule_retry_or_fail(failed_job, final_attempt_time, "permanent failure")

    assert failed_job.status is JobStatus.FAILED
    assert failed_job.retry_count == 2
    assert failed_job.started_at == final_attempt_time
    assert failed_job.finished_at == final_attempt_time
    assert failed_job.expires_at is None
    assert failed_job.error_message == "permanent failure"


def test_reaper_requeues_expired_lease():
    current_time = datetime.now(timezone.utc)
    stale_job = job(
        status=JobStatus.RUNNING,
        worker_id=uuid4(),
        started_at=current_time - timedelta(minutes=10),
        expires_at=current_time - timedelta(seconds=1),
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [stale_job]

    class Session:
        def execute(self, statement):
            return Result()

    recovered = reap_stale_jobs(Session(), current_time)

    assert recovered == 1
    assert stale_job.status is JobStatus.QUEUED
    assert stale_job.retry_count == 1
    assert stale_job.worker_id is None
    assert stale_job.error_message == "Job lease expired."


def test_worker_executes_claimed_job_and_marks_success(monkeypatch):
    worker_id = uuid4()
    job_id = uuid4()
    result_dataset_id = uuid4()
    job_data = {"id": job_id, "owner_id": "test-owner"}
    calls = []

    monkeypatch.setattr(worker, "claim_next_job", lambda claimed_by: job_data)
    monkeypatch.setattr(worker, "heartbeat_context", lambda *args: nullcontext())
    monkeypatch.setattr(worker, "execute_job", lambda data: {"result": True})
    saved_result_owners = []

    def save_result(result, owner_id):
        saved_result_owners.append(owner_id)
        return result_dataset_id

    monkeypatch.setattr(worker, "save_job_result", save_result)
    monkeypatch.setattr(
        worker,
        "mark_succeeded",
        lambda *args: calls.append(("succeeded", args)),
    )

    assert worker.process_next_job(worker_id) is True
    assert calls == [("succeeded", (job_id, worker_id, result_dataset_id))]
    assert saved_result_owners == ["test-owner"]


def test_worker_schedules_retry_for_execution_failure(monkeypatch):
    worker_id = uuid4()
    job_id = uuid4()
    job_data = {"id": job_id, "owner_id": "test-owner"}
    calls = []

    monkeypatch.setattr(worker, "claim_next_job", lambda claimed_by: job_data)
    monkeypatch.setattr(worker, "heartbeat_context", lambda *args: nullcontext())

    def fail_execution(data):
        raise RuntimeError("processing failed")

    monkeypatch.setattr(worker, "execute_job", fail_execution)
    monkeypatch.setattr(
        worker,
        "mark_failed",
        lambda *args: calls.append(("failed", args)),
    )

    assert worker.process_next_job(worker_id) is True
    assert calls == [("failed", (job_id, worker_id, "processing failed"))]
