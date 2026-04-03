from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.schemas.schemas import (
    FileUploadRequest,
    FileUploadResponse,
    FileResponse,
    FileListResponse,
    FileDeleteResponse,
)
from app.services.file_service import FileService
from app.core.exceptions import (
    ClientNotFoundError,
    FileNotFoundError,
    FileNotOwnedByClientError,
    StorageQuotaExceededError,
)

router = APIRouter(prefix="/clients/{client_id}/files", tags=["Files"])


def get_service(db: AsyncSession = Depends(get_db)) -> FileService:
    return FileService(db)


@router.post(
    "/",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file (quota-gated)",
)
async def upload_file(
    client_id: str,
    payload: FileUploadRequest,
    svc: FileService = Depends(get_service),
):
    """
    Register a file for a client after verifying storage quota.

    **Flow:**
    1. Check client exists.
    2. Verify `file_size_bytes` fits within remaining quota.
    3. Call storage backend (mock by default).
    4. Persist file metadata and update `used_storage_bytes`.

    Returns the created file record and updated storage stats.
    Rejects with **507 Insufficient Storage** if quota exceeded.
    """
    try:
        return await svc.upload_file(client_id, payload)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except StorageQuotaExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail={
                "error": "Storage quota exceeded",
                "client_id": e.client_id,
                "required_bytes": e.required,
                "available_bytes": e.available,
                "message": str(e),
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/",
    response_model=FileListResponse,
    summary="List all files for a client",
)
async def list_files(
    client_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    svc: FileService = Depends(get_service),
):
    """Return all file records belonging to a client (paginated)."""
    try:
        return await svc.list_files(client_id, skip=skip, limit=limit)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get(
    "/{file_id}",
    response_model=FileResponse,
    summary="Get a specific file record",
)
async def get_file(
    client_id: str,
    file_id: str,
    svc: FileService = Depends(get_service),
):
    """Retrieve metadata for a single file. Validates ownership."""
    try:
        return await svc.get_file(client_id, file_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except FileNotOwnedByClientError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.delete(
    "/{file_id}",
    response_model=FileDeleteResponse,
    summary="Delete a file and reclaim storage",
)
async def delete_file(
    client_id: str,
    file_id: str,
    svc: FileService = Depends(get_service),
):
    """
    Delete a file record and reclaim its storage bytes.

    - Validates file belongs to the specified client.
    - Calls storage backend for cleanup.
    - Updates `used_storage_bytes` atomically.
    """
    try:
        return await svc.delete_file(client_id, file_id)
    except ClientNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except FileNotOwnedByClientError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))