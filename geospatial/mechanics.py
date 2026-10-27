import contextlib
import threading
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session

LEASE_DURATION = timedelta(minutes=5)
HEARTBEAT_INTERVAL = 60 # seconds

# --- 1. SAFE WORKER CLAIM (FOR UPDATE SKIP LOCKED) ---
def claim_next_job(session: Session, worker_id: uuid.UUID) -> Job | None:
    now = datetime.now(timezone.utc)
    
    # Find a job that is either brand new or has timed out (backoff window passed)
    stmt = (
        select(Job)
        .where(
            and_(
                Job.status == JobStatus.QUEUED,
                or_(Job.expires_at == None, Job.expires_at <= now)
            )
        )
        .order_by(Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    
    job = session.scalars(stmt).first()
    if job:
        job.status = JobStatus.RUNNING
        job.worker_id = worker_id
        job.started_at = now
        job.expires_at = now + LEASE_DURATION
        session.commit()
        return job
        
    session.rollback()
    return None

# --- 2. WORKER HEARTBEAT (LEASE RENEWAL) ---
@contextlib.contextmanager
def heartbeat_context(session_factory, job_id: uuid.UUID, worker_id: uuid.UUID):
    stop_event = threading.Event()
    
    def heartbeat_loop():
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            with session_factory() as session:
                # Update lease only if worker still owns it and it's running
                stmt = select(Job).where(
                    Job.id == job_id,
                    Job.worker_id == worker_id,
                    Job.status == JobStatus.RUNNING
                ).with_for_update()
                
                job = session.scalars(stmt).first()
                if job:
                    job.expires_at = datetime.now(timezone.utc) + LEASE_DURATION
                    session.commit()
                else:
                    break # Job was aborted or modified elsewhere
                    
    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=2)

# --- 3. THE REAPER (STALE JOB RECOVERY WITH BACKOFF) ---
def reap_stale_jobs(session: Session):
    now = datetime.now(timezone.utc)
    
    # Fetch all running jobs whose leases have expired
    stmt = (
        select(Job)
        .where(and_(Job.status == JobStatus.RUNNING, Job.expires_at < now))
        .with_for_update(skip_locked=True)
    )
    stale_jobs = session.scalars(stmt).all()
    
    for job in stale_jobs:
        job.retry_count += 1
        job.worker_id = None
        job.started_at = None
        
        if job.retry_count >= job.max_retries:
            job.status = JobStatus.FAILED
            job.finished_at = now
            job.error_message = "Job lease expired. Max retries exceeded."
        else:
            job.status = JobStatus.QUEUED
            # Exponential Backoff for retries: 2^retry_count * 10 seconds
            backoff = timedelta(seconds=(2 ** job.retry_count) * 10)
            job.expires_at = now + backoff
            
    session.commit()
