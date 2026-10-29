"""Dataset storage implementations and factory."""
import os

from .base import DatasetStorage
from .local import LocalDatasetStorage


def configured_storage_backend() -> str:
    return os.environ.get("GEO_CLOUD_STORAGE_BACKEND", "local").strip().lower() or "local"


def get_dataset_storage(backend: str | None = None) -> DatasetStorage:
    selected = (backend or configured_storage_backend()).strip().lower()
    if selected == "local":
        return LocalDatasetStorage()
    raise ValueError(
        f"Unsupported dataset storage backend '{selected}'. "
        "Only 'local' is configured in this version."
    )


__all__ = ["DatasetStorage", "LocalDatasetStorage", "configured_storage_backend", "get_dataset_storage"]
