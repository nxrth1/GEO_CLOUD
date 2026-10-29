"""HTTP-facing dataset metadata and cursor helpers."""
import base64
import binascii
from datetime import datetime
import json
from uuid import UUID

from .models import Dataset


class InvalidDatasetCursor(ValueError):
    pass


def encode_dataset_cursor(record: Dataset) -> str:
    payload = json.dumps(
        {"created_at": record.created_at.isoformat(), "id": str(record.id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_dataset_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 2048:
        raise InvalidDatasetCursor("Invalid dataset pagination cursor.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.fromisoformat(payload["created_at"])
        dataset_id = UUID(payload["id"])
        if created_at.tzinfo is None:
            raise ValueError("Cursor timestamp must include a timezone.")
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, json.JSONDecodeError) as error:
        raise InvalidDatasetCursor("Invalid dataset pagination cursor.") from error
    return created_at, dataset_id


def dataset_metadata_response(record: Dataset) -> dict:
    return {
        "id": str(record.id),
        "name": record.name,
        "original_filename": record.original_filename,
        "format": record.format,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "feature_count": record.feature_count,
        "geometry_types": record.geometry_types,
        "bbox": record.bbox,
        "crs": record.crs,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
    }
