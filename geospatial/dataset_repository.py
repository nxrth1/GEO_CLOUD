"""Database operations for owner-scoped dataset metadata."""
from datetime import datetime, timezone
from io import BytesIO
import json
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .dataset_metadata import (
    calculate_geojson_metadata,
    normalized_dataset_name,
    sanitize_filename,
)
from .datasets import dataset_storage_key, delete_dataset, put_dataset_stream
from .models import Dataset, Job, JobStatus
from .storage import configured_storage_backend


def get_owned_dataset(
    session: Session,
    dataset_id: UUID,
    owner_id: str,
    *,
    for_update: bool = False,
) -> Dataset | None:
    statement = select(Dataset).where(
        Dataset.id == dataset_id,
        Dataset.owner_id == owner_id,
        Dataset.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def get_owned_datasets_for_update(
    session: Session,
    dataset_ids: list[UUID],
    owner_id: str,
) -> dict[UUID, Dataset]:
    """Lock active input records in deterministic order before creating a job."""
    unique_ids = sorted(set(dataset_ids), key=str)
    if not unique_ids:
        return {}
    records = session.execute(
        select(Dataset)
        .where(
            Dataset.id.in_(unique_ids),
            Dataset.owner_id == owner_id,
            Dataset.deleted_at.is_(None),
        )
        .order_by(Dataset.id)
        .with_for_update()
    ).scalars().all()
    return {record.id: record for record in records}


def list_owned_datasets(
    session: Session,
    owner_id: str,
    *,
    limit: int,
    before_created_at: datetime | None = None,
    before_id: UUID | None = None,
) -> tuple[list[Dataset], bool]:
    statement = select(Dataset).where(
        Dataset.owner_id == owner_id,
        Dataset.deleted_at.is_(None),
    )
    if before_created_at is not None and before_id is not None:
        statement = statement.where(
            or_(
                Dataset.created_at < before_created_at,
                (Dataset.created_at == before_created_at) & (Dataset.id < before_id),
            )
        )
    statement = statement.order_by(
        Dataset.created_at.desc(), Dataset.id.desc()
    ).limit(limit + 1)
    records = list(session.execute(statement).scalars().all())
    has_more = len(records) > limit
    return records[:limit], has_more


def _store_dataset_record(
    session: Session,
    *,
    source,
    owner_id: str,
    content: bytes,
    geojson: dict,
    name: str | None = None,
    original_filename: str | None = None,
) -> Dataset:
    dataset_id = uuid4()
    backend = configured_storage_backend()
    key = dataset_storage_key(dataset_id)
    filename = sanitize_filename(original_filename)
    metadata = calculate_geojson_metadata(geojson, content=content)
    record = Dataset(
        id=dataset_id,
        owner_id=owner_id,
        name=normalized_dataset_name(name, filename) or f"Dataset {str(dataset_id)[:8]}",
        original_filename=filename,
        storage_backend=backend,
        storage_key=key,
        **metadata,
    )

    put_dataset_stream(dataset_id, source, storage_backend=backend, storage_key=key)
    try:
        session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        try:
            delete_dataset(dataset_id, storage_backend=backend, storage_key=key)
        except Exception:
            # A later storage inventory/reconciliation can identify this orphan.
            pass
        raise
    return record


def save_owned_dataset_record(
    session: Session,
    geojson: dict,
    owner_id: str,
    *,
    name: str | None = None,
    original_filename: str | None = None,
) -> Dataset:
    content = json.dumps(
        geojson, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _store_dataset_record(
        session,
        source=BytesIO(content),
        owner_id=owner_id,
        content=content,
        geojson=geojson,
        name=name,
        original_filename=original_filename,
    )


def save_owned_dataset_from_stream(
    session: Session,
    *,
    source,
    geojson: dict,
    owner_id: str,
    size_bytes: int,
    sha256_hex: str,
    name: str | None = None,
    original_filename: str | None = None,
) -> Dataset:
    """Persist previously validated/staged bytes and their parsed GeoJSON."""
    # _store_dataset_record computes metadata from bytes. For uploads we don't
    # read a potentially large file into memory simply to hash it a second time;
    # the staged bytes are bounded, and this stream is read once into the storage
    # adapter. The caller supplies hash/size calculated during staging.
    dataset_id = uuid4()
    backend = configured_storage_backend()
    key = dataset_storage_key(dataset_id)
    filename = sanitize_filename(original_filename)
    record = Dataset(
        id=dataset_id,
        owner_id=owner_id,
        name=normalized_dataset_name(name, filename) or f"Dataset {str(dataset_id)[:8]}",
        original_filename=filename,
        format="geojson",
        content_type="application/geo+json",
        storage_backend=backend,
        storage_key=key,
        size_bytes=size_bytes,
        sha256=sha256_hex,
        **{
            key: value
            for key, value in calculate_geojson_metadata(geojson, content=b"").items()
            if key not in {"size_bytes", "sha256", "format", "content_type"}
        },
    )
    # The source must be rewound by the staging caller.
    put_dataset_stream(dataset_id, source, storage_backend=backend, storage_key=key)
    try:
        session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        try:
            delete_dataset(dataset_id, storage_backend=backend, storage_key=key)
        except Exception:
            pass
        raise
    return record


def save_owned_dataset(
    session: Session,
    geojson: dict,
    owner_id: str,
    *,
    name: str | None = None,
    original_filename: str | None = None,
) -> UUID:
    return save_owned_dataset_record(
        session,
        geojson,
        owner_id,
        name=name,
        original_filename=original_filename,
    ).id


def soft_delete_owned_dataset(
    session: Session, dataset_id: UUID, owner_id: str
) -> tuple[Dataset | None, bool]:
    """Soft-delete a dataset; bool is false when an active job still uses it."""
    record = session.execute(
        select(Dataset)
        .where(
            Dataset.id == dataset_id,
            Dataset.owner_id == owner_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if record is None:
        return None, False
    # DELETE is idempotent for a dataset the same owner has already deleted. If
    # storage cleanup remains pending, the route retries the idempotent removal.
    if record.deleted_at is not None:
        return record, True

    active_job_id = session.execute(
        select(Job.id)
        .where(
            Job.owner_id == owner_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            or_(
                Job.source_dataset_id == dataset_id,
                Job.secondary_dataset_id == dataset_id,
            ),
        )
        .limit(1)
    ).scalar_one_or_none()
    if active_job_id is not None:
        session.rollback()
        return record, False

    record.deleted_at = datetime.now(timezone.utc)
    session.commit()
    return record, True


def mark_dataset_storage_deleted(
    session: Session, dataset_id: UUID, storage_backend: str, storage_key: str
) -> bool:
    record = session.execute(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.storage_backend == storage_backend,
            Dataset.storage_key == storage_key,
            Dataset.deleted_at.is_not(None),
            Dataset.storage_deleted_at.is_(None),
        ).with_for_update()
    ).scalar_one_or_none()
    if record is None:
        return False
    record.storage_deleted_at = datetime.now(timezone.utc)
    session.commit()
    return True


def pending_dataset_cleanup(session: Session, *, limit: int = 10) -> list[Dataset]:
    return list(
        session.execute(
            select(Dataset)
            .where(
                Dataset.deleted_at.is_not(None),
                Dataset.storage_deleted_at.is_(None),
            )
            .order_by(Dataset.deleted_at, Dataset.id)
            .limit(limit)
        ).scalars().all()
    )
