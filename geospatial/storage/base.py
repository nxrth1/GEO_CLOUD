"""Storage contract used by dataset-management services.

Implementations store opaque, server-generated object keys. Callers never need to
know whether the bytes live on a local filesystem or in object storage.
"""
from typing import BinaryIO, Protocol


class DatasetStorage(Protocol):
    def put(self, key: str, source: BinaryIO) -> None:
        """Persist all bytes from the current position of ``source``."""
        ...

    def open(self, key: str) -> BinaryIO:
        """Open an object for binary reading. The caller closes the handle."""
        ...

    def delete(self, key: str) -> None:
        """Delete an object. Deleting a missing object should be harmless."""
        ...

    def exists(self, key: str) -> bool:
        """Return whether the object exists."""
        ...
