"""
Abstract storage backend.

This module defines the interface that any external storage system must implement.
Swap out LocalStorageBackend for S3StorageBackend, GCSStorageBackend, etc.
without touching any business logic.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StorageUploadResult:
    success: bool
    path: str
    message: str = ""


@dataclass
class StorageDeleteResult:
    success: bool
    message: str = ""


class AbstractStorageBackend(ABC):
    """
    Contract that every storage backend must fulfill.

    The service layer calls these methods; actual persistence
    (disk, S3, GCS, Azure Blob …) is handled by concrete subclasses.
    """

    @abstractmethod
    async def upload(self, client_id: str, file_path: str, file_name: str, file_size: int) -> StorageUploadResult:
        """
        Simulate / perform the upload of a file.
        Returns a StorageUploadResult indicating success/failure and the resolved path.
        """
        ...

    @abstractmethod
    async def delete(self, client_id: str, file_path: str) -> StorageDeleteResult:
        """
        Simulate / perform the deletion of a file.
        Returns a StorageDeleteResult indicating success/failure.
        """
        ...

    @abstractmethod
    async def exists(self, client_id: str, file_path: str) -> bool:
        """Check whether a file exists at the given logical path."""
        ...


class MockStorageBackend(AbstractStorageBackend):
    """
    Default no-op backend used in development / testing.

    No actual I/O is performed — it simply acknowledges every
    operation as successful.  Replace this with a real implementation
    (LocalStorageBackend, S3StorageBackend, etc.) in production.
    """

    async def upload(self, client_id: str, file_path: str, file_name: str, file_size: int) -> StorageUploadResult:
        # In a real backend: open(file_path, 'wb').write(data)  or  s3.put_object(...)
        return StorageUploadResult(
            success=True,
            path=file_path,
            message=f"[MockBackend] '{file_name}' acknowledged at '{file_path}'.",
        )

    async def delete(self, client_id: str, file_path: str) -> StorageDeleteResult:
        # In a real backend: os.remove(file_path)  or  s3.delete_object(...)
        return StorageDeleteResult(
            success=True,
            message=f"[MockBackend] Deletion of '{file_path}' acknowledged.",
        )

    async def exists(self, client_id: str, file_path: str) -> bool:
        # In a real backend: os.path.exists(file_path)  or  s3.head_object(...)
        return True


# ── Singleton used throughout the application ───────────────────
# To swap backends, replace MockStorageBackend with your implementation.
storage_backend: AbstractStorageBackend = MockStorageBackend()
