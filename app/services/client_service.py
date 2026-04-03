"""
Client service — all business logic for client management.
No HTTP-level concerns here; only DB + domain rules.
"""
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Client
from app.schemas.schemas import ClientCreate, ClientUpdate, ClientResponse, ClientListResponse
from app.core.exceptions import (
    ClientNotFoundError,
    ClientAlreadyExistsError,
    InvalidStorageLimitError,
)
from app.core.config import settings


class ClientService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── Internal helpers ────────────────────────────────────────

    async def _get_or_raise(self, client_id: str) -> Client:
        result = await self.db.execute(select(Client).where(Client.id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            raise ClientNotFoundError(client_id)
        return client

    # ─── Public API ──────────────────────────────────────────────

    async def create_client(self, payload: ClientCreate) -> ClientResponse:
        client = Client(
            name=payload.name,
            storage_limit_bytes=payload.storage_limit_bytes,
            used_storage_bytes=0,
        )
        self.db.add(client)
        await self.db.flush()   # get the auto-generated id before commit
        await self.db.refresh(client)
        return ClientResponse.from_orm_model(client)

    async def get_client(self, client_id: str) -> ClientResponse:
        client = await self._get_or_raise(client_id)
        return ClientResponse.from_orm_model(client)

    async def list_clients(self, skip: int = 0, limit: int = 50) -> ClientListResponse:
        count_result = await self.db.execute(select(func.count()).select_from(Client))
        total = count_result.scalar_one()

        result = await self.db.execute(
            select(Client).order_by(Client.created_at.desc()).offset(skip).limit(limit)
        )
        clients = result.scalars().all()
        return ClientListResponse(
            total=total,
            clients=[ClientResponse.from_orm_model(c) for c in clients],
        )

    async def update_client(self, client_id: str, payload: ClientUpdate) -> ClientResponse:
        client = await self._get_or_raise(client_id)

        if payload.name is not None:
            client.name = payload.name

        if payload.storage_limit_bytes is not None:
            new_limit = payload.storage_limit_bytes
            if new_limit < client.used_storage_bytes:
                raise InvalidStorageLimitError(
                    f"New limit ({new_limit} bytes) is less than currently used "
                    f"storage ({client.used_storage_bytes} bytes). "
                    f"Delete files first before lowering the quota."
                )
            if new_limit > settings.MAX_STORAGE_LIMIT_BYTES:
                raise InvalidStorageLimitError(
                    f"Limit cannot exceed {settings.MAX_STORAGE_LIMIT_BYTES} bytes."
                )
            client.storage_limit_bytes = new_limit

        client.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(client)
        return ClientResponse.from_orm_model(client)

    async def delete_client(self, client_id: str) -> None:
        client = await self._get_or_raise(client_id)
        await self.db.delete(client)

    async def recalculate_storage(self, client_id: str) -> ClientResponse:
        """
        Recalculates used_storage_bytes from actual file records.
        Useful for repair / consistency checks.
        """
        from app.models.models import FileRecord
        from sqlalchemy import func as sqlfunc

        client = await self._get_or_raise(client_id)
        result = await self.db.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(FileRecord.file_size_bytes), 0))
            .where(FileRecord.client_id == client_id)
        )
        actual_used = result.scalar_one()

        if client.used_storage_bytes != actual_used:
            client.used_storage_bytes = actual_used
            client.updated_at = datetime.now(timezone.utc)
            await self.db.flush()
            await self.db.refresh(client)

        return ClientResponse.from_orm_model(client)
