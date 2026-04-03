# File Storage Quota Service

A production-ready **FastAPI** backend that manages per-client file metadata and storage quotas backed by **SQLite** (swappable to PostgreSQL). Files are never physically stored — the service enforces quota rules, persists metadata, and delegates actual I/O to a pluggable storage backend.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Project Structure](#project-structure)
3. [API Reference](#api-reference)
4. [Design Decisions](#design-decisions)
5. [Swapping the Storage Backend](#swapping-the-storage-backend)
6. [Running Tests](#running-tests)
7. [Environment Variables](#environment-variables)

---

## Quick Start

```bash
# 1. Clone / unzip the project
cd storage_service

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Copy env template
cp .env.example .env

# 5. Start the server
python run.py
# → http://localhost:8000
# → Swagger UI: http://localhost:8000/docs
# → ReDoc:      http://localhost:8000/redoc
```

The SQLite database file (`storage_service.db`) is created automatically on first run.

---

## Project Structure

```
storage_service/
├── app/
│   ├── main.py                  # FastAPI app factory + lifespan
│   ├── api/
│   │   ├── router.py            # Aggregates all route groups
│   │   └── routes/
│   │       ├── clients.py       # /api/v1/clients  endpoints
│   │       └── files.py         # /api/v1/clients/{id}/files  endpoints
│   ├── core/
│   │   ├── config.py            # Pydantic settings (reads .env)
│   │   └── exceptions.py        # Domain-specific exception types
│   ├── db/
│   │   └── database.py          # Async SQLAlchemy engine + session factory
│   ├── models/
│   │   └── models.py            # ORM models: Client, FileRecord
│   ├── schemas/
│   │   └── schemas.py           # Pydantic request/response models
│   ├── services/
│   │   ├── client_service.py    # Client CRUD + storage repair
│   │   └── file_service.py      # Quota-gated upload / delete
│   └── storage/
│       └── backend.py           # AbstractStorageBackend + MockStorageBackend
├── tests/
│   └── test_api.py              # 19 integration tests (in-memory SQLite)
├── run.py                       # Uvicorn entry point
├── pytest.ini
├── requirements.txt
└── .env.example
```

---

## API Reference

### Health

| Method | Path      | Description          |
|--------|-----------|----------------------|
| GET    | `/health` | Service health check |

---

### Clients  `/api/v1/clients`

| Method | Path                                    | Description                                      |
|--------|-----------------------------------------|--------------------------------------------------|
| POST   | `/`                                     | Create a client with a storage quota             |
| GET    | `/`                                     | List all clients (paginated)                     |
| GET    | `/{client_id}`                          | Get client + live storage stats                  |
| PATCH  | `/{client_id}`                          | Update name and/or quota                         |
| DELETE | `/{client_id}`                          | Delete client + cascade-delete all files         |
| POST   | `/{client_id}/recalculate-storage`      | Repair quota counter from actual file records    |

#### Create a client

```bash
curl -X POST http://localhost:8000/api/v1/clients/ \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "storage_limit_mb": 500}'
```

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "name": "Acme Corp",
  "storage": {
    "total_bytes": 524288000,
    "used_bytes": 0,
    "remaining_bytes": 524288000,
    "total_mb": 500.0,
    "used_mb": 0.0,
    "remaining_mb": 500.0,
    "usage_percent": 0.0
  },
  "created_at": "2025-01-01T12:00:00Z",
  "updated_at": "2025-01-01T12:00:00Z"
}
```

---

### Files  `/api/v1/clients/{client_id}/files`

| Method | Path          | Description                              |
|--------|---------------|------------------------------------------|
| POST   | `/`           | Upload file — quota-gated                |
| GET    | `/`           | List all files for the client            |
| GET    | `/{file_id}`  | Get a single file record                 |
| DELETE | `/{file_id}`  | Delete file + reclaim storage            |

#### Upload a file (success)

```bash
curl -X POST http://localhost:8000/api/v1/clients/{client_id}/files/ \
  -H "Content-Type: application/json" \
  -d '{
    "file_name": "report.pdf",
    "file_size_bytes": 5242880,
    "file_path": "/uploads/acme/report.pdf",
    "mime_type": "application/pdf"
  }'
```

```json
{
  "file": {
    "id": "abc123...",
    "client_id": "3fa85f64...",
    "file_name": "report.pdf",
    "file_size_bytes": 5242880,
    "file_size_mb": 5.0,
    "file_path": "/uploads/acme/report.pdf",
    "mime_type": "application/pdf",
    "created_at": "2025-01-01T12:01:00Z"
  },
  "storage_after": {
    "total_bytes": 524288000,
    "used_bytes": 5242880,
    "remaining_bytes": 519045120,
    "total_mb": 500.0,
    "used_mb": 5.0,
    "remaining_mb": 495.0,
    "usage_percent": 1.0
  },
  "message": "File uploaded and metadata recorded successfully."
}
```

#### Upload rejected — quota exceeded

```json
HTTP 507 Insufficient Storage
{
  "detail": {
    "error": "Storage quota exceeded",
    "client_id": "3fa85f64...",
    "required_bytes": 600000000,
    "available_bytes": 519045120,
    "message": "Client '...' has insufficient storage. Required: 600000000 bytes, Available: 519045120 bytes."
  }
}
```

#### Delete a file

```bash
curl -X DELETE http://localhost:8000/api/v1/clients/{client_id}/files/{file_id}
```

```json
{
  "deleted_file_id": "abc123...",
  "storage_after": { "used_bytes": 0, ... },
  "message": "File 'abc123...' deleted. 5242880 bytes reclaimed."
}
```

---

## Design Decisions

### 1. Quota enforcement with row-level locking

When a file is uploaded or deleted, the `Client` row is fetched with `SELECT … FOR UPDATE`. This prevents two concurrent requests from both passing the quota check with the same remaining balance and together exceeding the limit. The quota counter and the file record are written in the **same transaction**, so partial failures are impossible.

### 2. Atomic counter + `recalculate-storage` repair endpoint

`used_storage_bytes` is maintained as a running counter (fast reads, no aggregation on every request). The `POST /recalculate-storage` endpoint re-sums all file sizes from the `file_records` table, acting as a self-healing tool if any counter drift ever occurs.

### 3. Pluggable storage backend

All physical I/O is hidden behind `AbstractStorageBackend`:

```
Service layer  →  AbstractStorageBackend.upload() / delete() / exists()
                         ↑
               MockStorageBackend (default — no-op)
               LocalStorageBackend  (write to disk)
               S3StorageBackend     (boto3)
               GCSStorageBackend    (google-cloud-storage)
```

The service layer has **zero knowledge** of where files live. Swapping backends requires changing one line in `app/storage/backend.py`.

### 4. HTTP 507 for quota errors

RFC 2518 defines `507 Insufficient Storage` exactly for this case — more accurate than 400 or 422, and gives clients a machine-readable signal to handle quota errors distinctly from validation errors.

### 5. Domain exceptions → HTTP translation at the router layer

Services raise typed domain exceptions (`StorageQuotaExceededError`, `ClientNotFoundError`, etc.). Routes catch them and map to HTTP status codes. This keeps services fully testable without an HTTP stack.

### 6. Lowering quota below current usage is rejected

`PATCH /clients/{id}` with a new limit lower than `used_storage_bytes` returns `422`. Clients must delete files first before shrinking their quota. This prevents the system from entering an invalid state where `used > limit`.

---

## Swapping the Storage Backend

Open `app/storage/backend.py` and implement `AbstractStorageBackend`:

```python
import boto3
from app.storage.backend import AbstractStorageBackend, StorageUploadResult, StorageDeleteResult

class S3StorageBackend(AbstractStorageBackend):
    def __init__(self):
        self.s3 = boto3.client("s3")
        self.bucket = "my-bucket"

    async def upload(self, client_id, file_path, file_name, file_size):
        # self.s3.put_object(Bucket=self.bucket, Key=file_path, Body=b"")
        return StorageUploadResult(success=True, path=file_path)

    async def delete(self, client_id, file_path):
        # self.s3.delete_object(Bucket=self.bucket, Key=file_path)
        return StorageDeleteResult(success=True)

    async def exists(self, client_id, file_path):
        # head = self.s3.head_object(Bucket=self.bucket, Key=file_path)
        return True

# Replace the singleton at the bottom of backend.py:
storage_backend = S3StorageBackend()
```

No other files need to change.

---

## Running Tests

```bash
# All 19 tests, in-memory SQLite — no setup required
pytest -v

# With coverage
pip install pytest-cov
pytest --cov=app --cov-report=term-missing
```

Test coverage includes: client CRUD, file upload success, quota rejection, exact-boundary quota, multi-client file isolation, ownership enforcement on get/delete, storage recalculation, health check.

---

## Environment Variables

| Variable                      | Default                              | Description                        |
|-------------------------------|--------------------------------------|------------------------------------|
| `DATABASE_URL`                | `sqlite+aiosqlite:///./storage_service.db` | Async DB URL             |
| `DEFAULT_STORAGE_LIMIT_BYTES` | `104857600` (100 MB)                 | Quota when not specified           |
| `MAX_STORAGE_LIMIT_BYTES`     | `10737418240` (10 GB)                | Hard ceiling on any quota          |
| `DEBUG`                       | `false`                              | Enables SQLAlchemy echo            |

All variables can be set in a `.env` file (copy from `.env.example`).
