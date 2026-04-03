from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator
from app.core.config import settings


# ─────────────────────────── Helpers ────────────────────────────

def bytes_to_mb(b: int) -> float:
    return round(b / (1024 * 1024), 4)


# ─────────────────────────── Client Schemas ─────────────────────


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Display name for the client")
    storage_limit_bytes: int = Field(
        default=None,
        description="Storage limit in bytes. Defaults to system default if not provided."
    )
    storage_limit_mb: Optional[float] = Field(
        default=None,
        description="Storage limit in MB (alternative to bytes). Takes precedence over storage_limit_bytes if both given."
    )

    @model_validator(mode="after")
    def resolve_storage_limit(self):
        if self.storage_limit_mb is not None:
            self.storage_limit_bytes = int(self.storage_limit_mb * 1024 * 1024)
        if self.storage_limit_bytes is None:
            self.storage_limit_bytes = settings.DEFAULT_STORAGE_LIMIT_BYTES
        if self.storage_limit_bytes <= 0:
            raise ValueError("Storage limit must be a positive number.")
        if self.storage_limit_bytes > settings.MAX_STORAGE_LIMIT_BYTES:
            raise ValueError(
                f"Storage limit cannot exceed {bytes_to_mb(settings.MAX_STORAGE_LIMIT_BYTES)} MB."
            )
        return self


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    storage_limit_bytes: Optional[int] = Field(None, gt=0)
    storage_limit_mb: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def resolve_storage_limit(self):
        if self.storage_limit_mb is not None:
            self.storage_limit_bytes = int(self.storage_limit_mb * 1024 * 1024)
        return self


class StorageInfo(BaseModel):
    total_bytes: int
    used_bytes: int
    remaining_bytes: int
    total_mb: float
    used_mb: float
    remaining_mb: float
    usage_percent: float


class ClientResponse(BaseModel):
    id: str
    name: str
    storage: StorageInfo
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, client) -> "ClientResponse":
        total = client.storage_limit_bytes
        used = client.used_storage_bytes
        remaining = client.remaining_storage_bytes
        return cls(
            id=client.id,
            name=client.name,
            storage=StorageInfo(
                total_bytes=total,
                used_bytes=used,
                remaining_bytes=remaining,
                total_mb=bytes_to_mb(total),
                used_mb=bytes_to_mb(used),
                remaining_mb=bytes_to_mb(remaining),
                usage_percent=round((used / total) * 100, 2) if total > 0 else 0.0,
            ),
            created_at=client.created_at,
            updated_at=client.updated_at,
        )


class ClientListResponse(BaseModel):
    total: int
    clients: list[ClientResponse]


# ─────────────────────────── File Schemas ───────────────────────


class FileUploadRequest(BaseModel):
    file_name: str = Field(..., min_length=1, max_length=512, description="Original file name")
    file_size_bytes: int = Field(..., gt=0, description="File size in bytes")
    file_path: str = Field(..., min_length=1, max_length=1024, description="Logical storage path / reference")
    mime_type: Optional[str] = Field(None, max_length=128, description="MIME type (optional)")


class FileResponse(BaseModel):
    id: str
    client_id: str
    file_name: str
    file_size_bytes: int
    file_size_mb: float
    file_path: str
    mime_type: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_model(cls, f) -> "FileResponse":
        return cls(
            id=f.id,
            client_id=f.client_id,
            file_name=f.file_name,
            file_size_bytes=f.file_size_bytes,
            file_size_mb=bytes_to_mb(f.file_size_bytes),
            file_path=f.file_path,
            mime_type=f.mime_type,
            created_at=f.created_at,
        )


class FileListResponse(BaseModel):
    client_id: str
    total_files: int
    files: list[FileResponse]


class FileUploadResponse(BaseModel):
    file: FileResponse
    storage_after: StorageInfo
    message: str


class FileDeleteResponse(BaseModel):
    deleted_file_id: str
    storage_after: StorageInfo
    message: str


# ─────────────────────────── Generic ────────────────────────────


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None