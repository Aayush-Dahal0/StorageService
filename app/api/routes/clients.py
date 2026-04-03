from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.schemas.schemas import (
    ClientCreate,
    ClientUpdate,
    ClientResponse,
    ClientListResponse,
    ErrorResponse,
)
from app.services.client_service import ClientService
from app.core.exceptions import (
    ClientNotFoundError,
    InvalidStorageLimitError,
)

router = APIRouter(prefix="/clients", tags=["Clients"])


def get_service(db: AsyncSession = Depends(get_db)) -> ClientService:
    return ClientService(db)


@router.post(
    "/",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new client",
)
async def create_client(
    payload: ClientCreate,
    svc: ClientService = Depends(get_service),
):
    """
    Register a new client with a storage quota.

    - **name**: Display name for the client
    - **storage_limit_bytes**: Quota in bytes (optional, defaults to 100 MB)
    - **storage_limit_mb**: Quota in MB (optional, takes precedence if provided)
    """
    return await svc.create_client(payload)


@router.get(
    "/",
    response_model=ClientListResponse,
    summary="List all clients",
)
async def list_clients(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    svc: ClientService = Depends(get_service),
):
    """Return a paginated list of all clients with their storage summaries."""
    return await svc.list_clients(skip=skip, limit=limit)


@router.get(
    "/{client_id}",
    response_model=ClientResponse,
    summary="Get a client by ID",
)
async def get_client(
    client_id: str,
    svc: ClientService = Depends(get_service),
):
    """Fetch a single client including real-time storage statistics."""
    try:
        return await svc.get_client(client_id)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.patch(
    "/{client_id}",
    response_model=ClientResponse,
    summary="Update a client",
)
async def update_client(
    client_id: str,
    payload: ClientUpdate,
    svc: ClientService = Depends(get_service),
):
    """
    Update client name and/or storage limit.

    - Raising the limit is always allowed.
    - Lowering the limit below current usage is **rejected**.
    """
    try:
        return await svc.update_client(client_id, payload)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidStorageLimitError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a client and all their files",
)
async def delete_client(
    client_id: str,
    svc: ClientService = Depends(get_service),
):
    """
    Permanently delete a client. All associated file records are cascade-deleted.
    """
    try:
        await svc.delete_client(client_id)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post(
    "/{client_id}/recalculate-storage",
    response_model=ClientResponse,
    summary="Recalculate storage from file records",
)
async def recalculate_storage(
    client_id: str,
    svc: ClientService = Depends(get_service),
):
    """
    Recompute `used_storage_bytes` by summing actual file records.
    Use this to repair any drift caused by interrupted transactions.
    """
    try:
        return await svc.recalculate_storage(client_id)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))