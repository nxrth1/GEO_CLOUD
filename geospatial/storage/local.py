"""Atomic local-filesystem implementation of the dataset storage contract."""
import os
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import BinaryIO


class LocalDatasetStorage:
    def __init__(self, root: Path | str | None = None):
        configured = root or os.environ.get("GEO_CLOUD_DATASETS_DIR")
        self.root = (
            Path(configured).expanduser()
            if configured
            else Path(__file__).resolve().parents[2] / "data" / "datasets"
        ).resolve()

    def _path_for(self, key: str) -> Path:
        if not isinstance(key, str) or not key or "\\" in key or "\x00" in key:
            raise ValueError("Invalid dataset storage key.")
        relative = PurePosixPath(key)
        if not relative.parts or relative.is_absolute() or any(
            part in ("", ".", "..") for part in relative.parts
        ):
            raise ValueError("Invalid dataset storage key.")
        path = (self.root / Path(*relative.parts)).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("Dataset storage key escapes the storage root.")
        return path

    def put(self, key: str, source: BinaryIO) -> None:
        destination = self._path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb", dir=destination.parent, suffix=".tmp", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    temporary_file.write(chunk)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self._path_for(key).open("rb")

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()
