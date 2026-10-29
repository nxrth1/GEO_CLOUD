"""Dataset creation pipeline: bounded upload, parse, validate, metadata and storage."""
import hashlib
import json
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from .dataset_repository import save_owned_dataset_from_stream, save_owned_dataset_record
from .processes import validate_json
from .settings import get_max_upload_bytes


class DatasetTooLargeError(ValueError):
    """Dataset bytes exceed the configured upload limit."""


class DatasetUploadError(ValueError):
    """Uploaded bytes are not a readable GeoJSON FeatureCollection."""


class DatasetValidationError(ValueError):
    def __init__(self, validation: dict):
        super().__init__("Dataset failed GeoJSON validation.")
        self.validation = validation


def serialize_geojson(geojson: dict) -> bytes:
    try:
        return json.dumps(
            geojson, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DatasetUploadError("Dataset must be JSON-serializable GeoJSON.") from error


def validate_dataset_size(geojson: dict) -> int:
    size = len(serialize_geojson(geojson))
    if size > get_max_upload_bytes():
        raise DatasetTooLargeError(
            f"Dataset exceeds the {get_max_upload_bytes()} byte upload limit."
        )
    return size


def create_dataset_from_geojson(
    session,
    geojson: dict,
    owner_id: str,
    *,
    name: str | None = None,
):
    size = validate_dataset_size(geojson)
    validation = validate_json(geojson)
    if not validation["accepted"]:
        raise DatasetValidationError(validation)
    record = save_owned_dataset_record(session, geojson, owner_id, name=name)
    return record, validation, size


def create_dataset_from_upload(
    session,
    source: BinaryIO,
    owner_id: str,
    *,
    name: str | None = None,
    original_filename: str | None = None,
):
    """Stage a multipart upload with a hard byte cap before parsing or storage."""
    maximum = get_max_upload_bytes()
    byte_count = 0
    digest = hashlib.sha256()

    with NamedTemporaryFile(mode="w+b", suffix=".geojson.upload", delete=True) as staged:
        while True:
            chunk = source.read(64 * 1024)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            byte_count += len(chunk)
            if byte_count > maximum:
                raise DatasetTooLargeError(
                    f"Dataset exceeds the {maximum} byte upload limit."
                )
            digest.update(chunk)
            staged.write(chunk)

        if byte_count == 0:
            raise DatasetUploadError("Uploaded file is empty.")
        staged.flush()
        staged.seek(0)
        try:
            geojson = json.load(staged)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
            raise DatasetUploadError("Uploaded file is not valid UTF-8 JSON.") from error

        validation = validate_json(geojson)
        if not validation["accepted"]:
            raise DatasetValidationError(validation)

        staged.seek(0)
        record = save_owned_dataset_from_stream(
            session,
            source=staged,
            geojson=geojson,
            owner_id=owner_id,
            size_bytes=byte_count,
            sha256_hex=digest.hexdigest(),
            name=name,
            original_filename=original_filename,
        )

    return record, validation
