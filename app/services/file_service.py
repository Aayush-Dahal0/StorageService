"""
File service — quota enforcement + file record CRUD.
Decoupled from HTTP and from physical storage via AbstractStorageBackend.
"""
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Client, FileRecord
from app.schemas.schemas import (
    FileUploadRequest,
    FileResponse,
    FileListResponse,
    FileUploadResponse,
    FileDeleteResponse,
    StorageInfo,
)
from app.core.exceptions import (
    ClientNotFoundError,
    FileNotFoundError,
    FileNotOwnedByClientError,
    StorageQuotaExceededError,
)
from app.storage.backend import storage_backend


def _storage_info(client: Client) -> StorageInfo:
    from app.schemas.schemas import bytes_to_mb
    total = client.storage_limit_bytes
    used = client.used_storage_bytes
    remaining = client.remaining_storage_bytes
    return StorageInfo(
        total_bytes=total,
        used_bytes=used,
        remaining_bytes=remaining,
        total_mb=bytes_to_mb(total),
        used_mb=bytes_to_mb(used),
        remaining_mb=bytes_to_mb(remaining),
        usage_percent=round((used / total) * 100, 2) if total > 0 else 0.0,
    )


class FileService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── Internal helpers ────────────────────────────────────────

    async def _get_client_or_raise(self, client_id: str) -> Client:
        result = await self.db.execute(
            select(Client).where(Client.id == client_id).with_for_update()
        )
        client = result.scalar_one_or_none()
        if client is None:
            raise ClientNotFoundError(client_id)
        return client

    async def _get_file_or_raise(self, file_id: str) -> FileRecord:
        result = await self.db.execute(
            select(FileRecord).where(FileRecord.id == file_id)
        )
        file_record = result.scalar_one_or_none()
        if file_record is None:
            raise FileNotFoundError(file_id)
        return file_record

    # ─── Public API ──────────────────────────────────────────────

    async def upload_file(self, client_id: str, payload: FileUploadRequest) -> FileUploadResponse:
        """
        Full quota-gated upload flow:

        1. Lock the client row (SELECT … FOR UPDATE) to prevent races.
        2. Check remaining quota.
        3. Delegate to the storage backend (abstractly).
        4. Persist file metadata + update used_storage_bytes atomically.
        """
        client = await self._get_client_or_raise(client_id)

        # ── Quota check ──────────────────────────────────────────
        if not client.has_enough_storage(payload.file_size_bytes):
            raise StorageQuotaExceededError(
                client_id=client_id,
                required=payload.file_size_bytes,
                available=client.remaining_storage_bytes,
            )

        # ── Storage backend call (abstract) ──────────────────────
        upload_result = await storage_backend.upload(
            client_id=client_id,
            file_path=payload.file_path,
            file_name=payload.file_name,
            file_size=payload.file_size_bytes,
        )

        if not upload_result.success:
            raise RuntimeError(f"Storage backend rejected upload: {upload_result.message}")

        # ── Persist metadata ─────────────────────────────────────
        file_record = FileRecord(
            client_id=client_id,
            file_name=payload.file_name,
            file_size_bytes=payload.file_size_bytes,
            file_path=upload_result.path,
            mime_type=payload.mime_type,
        )
        self.db.add(file_record)

        # Update quota atomically in the same transaction
        client.used_storage_bytes += payload.file_size_bytes
        client.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.refresh(file_record)
        await self.db.refresh(client)

        return FileUploadResponse(
            file=FileResponse.from_orm_model(file_record),
            storage_after=_storage_info(client),
            message="File uploaded and metadata recorded successfully.",
        )

    async def list_files(
        self,
        client_id: str,
        skip: int = 0,
        limit: int = 50,
    ) -> FileListResponse:
        # Ensure client exists
        result = await self.db.execute(select(Client).where(Client.id == client_id))
        if result.scalar_one_or_none() is None:
            raise ClientNotFoundError(client_id)

        count_result = await self.db.execute(
            select(func.count())
            .select_from(FileRecord)
            .where(FileRecord.client_id == client_id)
        )
        total = count_result.scalar_one()

        files_result = await self.db.execute(
            select(FileRecord)
            .where(FileRecord.client_id == client_id)
            .order_by(FileRecord.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        files = files_result.scalars().all()

        return FileListResponse(
            client_id=client_id,
            total_files=total,
            files=[FileResponse.from_orm_model(f) for f in files],
        )

    async def get_file(self, client_id: str, file_id: str) -> FileResponse:
        file_record = await self._get_file_or_raise(file_id)
        if file_record.client_id != client_id:
            raise FileNotOwnedByClientError(file_id, client_id)
        return FileResponse.from_orm_model(file_record)

    async def delete_file(self, client_id: str, file_id: str) -> FileDeleteResponse:
        """
        Delete flow:

        1. Lock client row.
        2. Verify file belongs to this client.
        3. Delegate deletion to storage backend.
        4. Remove metadata + reclaim quota atomically.
        """
        client = await self._get_client_or_raise(client_id)
        file_record = await self._get_file_or_raise(file_id)

        if file_record.client_id != client_id:
            raise FileNotOwnedByClientError(file_id, client_id)

        # ── Storage backend call ─────────────────────────────────
        delete_result = await storage_backend.delete(
            client_id=client_id,
            file_path=file_record.file_path,
        )
        if not delete_result.success:
            raise RuntimeError(f"Storage backend rejected deletion: {delete_result.message}")

        # ── Reclaim quota ────────────────────────────────────────
        freed = file_record.file_size_bytes
        await self.db.delete(file_record)

        client.used_storage_bytes = max(0, client.used_storage_bytes - freed)
        client.updated_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.refresh(client)

        return FileDeleteResponse(
            deleted_file_id=file_id,
            storage_after=_storage_info(client),
            message=f"File '{file_id}' deleted. {freed} bytes reclaimed.",
        )
