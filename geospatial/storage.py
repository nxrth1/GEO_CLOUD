# geospatial/storage.py
from abc import ABC, abstractmethod
from pathlib import Path

class StorageProvider(ABC):
    @abstractmethod
    def save(self, key: str, data: bytes) -> str:
        """Saves data bytes under the given key/path and returns storage identifier."""
        pass

    @abstractmethod
    def load(self, key: str) -> bytes:
        """Retrieves raw bytes for a given key/path."""
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """Deletes object at the specified key/path."""
        pass


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_directory: Path):
        self.base_directory = base_directory
        self.base_directory.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        return self.base_directory / key

    def save(self, key: str, data: bytes) -> str:
        file_path = self._resolve_path(key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temporary file then replace atomically
        tmp_path = file_path.with_suffix(".tmp")
        tmp_path.write_bytes(data)
        tmp_path.replace(file_path)
        return str(key)

    def load(self, key: str) -> bytes:
        file_path = self._resolve_path(key)
        if not file_path.exists():
            raise FileNotFoundError(f"Storage key {key} not found.")
        return file_path.read_bytes()

    def delete(self, key: str) -> None:
        file_path = self._resolve_path(key)
        if file_path.exists():
            file_path.unlink()