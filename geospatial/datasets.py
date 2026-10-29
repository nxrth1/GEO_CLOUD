"""Dataset byte storage helpers, backed by the configured storage adapter."""
import io
import json
from typing import BinaryIO, Any
from uuid import UUID, uuid4

from .storage import configured_storage_backend, get_dataset_storage


def dataset_storage_key(dataset_id: UUID) -> str:
    # Retains compatibility with the original local `<uuid>.json` layout.
    return f"{dataset_id}.json"


def put_dataset_stream(
    dataset_id: UUID,
    source: BinaryIO,
    *,
    storage_backend: str | None = None,
    storage_key: str | None = None,
) -> None:
    backend = storage_backend or configured_storage_backend()
    key = storage_key or dataset_storage_key(dataset_id)
    get_dataset_storage(backend).put(key, source)


def save_dataset(geojson: dict[str, Any]) -> UUID:
    dataset_id = uuid4()
    content = json.dumps(
        geojson, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    put_dataset_stream(dataset_id, io.BytesIO(content))
    return dataset_id


def load_dataset(
    dataset_id: UUID,
    *,
    storage_backend: str | None = None,
    storage_key: str | None = None,
):
    backend = storage_backend or configured_storage_backend()
    key = storage_key or dataset_storage_key(dataset_id)
    with get_dataset_storage(backend).open(key) as stored_file:
        return json.load(stored_file)


def load_dataset_bytes(
    dataset_id: UUID,
    *,
    storage_backend: str | None = None,
    storage_key: str | None = None,
) -> bytes:
    backend = storage_backend or configured_storage_backend()
    key = storage_key or dataset_storage_key(dataset_id)
    with get_dataset_storage(backend).open(key) as stored_file:
        return stored_file.read()


def delete_dataset(
    dataset_id: UUID,
    *,
    storage_backend: str | None = None,
    storage_key: str | None = None,
) -> None:
    backend = storage_backend or configured_storage_backend()
    key = storage_key or dataset_storage_key(dataset_id)
    get_dataset_storage(backend).delete(key)


def dataset_exists(
    dataset_id: UUID,
    *,
    storage_backend: str | None = None,
    storage_key: str | None = None,
) -> bool:
    backend = storage_backend or configured_storage_backend()
    key = storage_key or dataset_storage_key(dataset_id)
    return get_dataset_storage(backend).exists(key)
