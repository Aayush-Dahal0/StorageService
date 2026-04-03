"""
Integration tests for the Storage Quota Service.
Uses an in-memory SQLite DB so no cleanup is needed between runs.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.db.database import Base, get_db

# ── Test DB setup ────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.dependency_overrides[get_db] = override_get_db
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── Helpers ──────────────────────────────────────────────────────

async def create_test_client(client, name="TestCo", storage_mb=10):
    resp = await client.post(
        "/api/v1/clients/",
        json={"name": name, "storage_limit_mb": storage_mb},
    )
    assert resp.status_code == 201
    return resp.json()


async def upload_test_file(client, client_id, name="file.txt", size_bytes=1024):
    resp = await client.post(
        f"/api/v1/clients/{client_id}/files/",
        json={
            "file_name": name,
            "file_size_bytes": size_bytes,
            "file_path": f"/storage/{client_id}/{name}",
            "mime_type": "text/plain",
        },
    )
    return resp


# ── Client tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_client(client):
    resp = await client.post("/api/v1/clients/", json={"name": "Acme", "storage_limit_mb": 100})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Acme"
    assert data["storage"]["total_mb"] == 100.0
    assert data["storage"]["used_mb"] == 0.0
    assert data["storage"]["remaining_mb"] == 100.0
    assert data["storage"]["usage_percent"] == 0.0


@pytest.mark.asyncio
async def test_create_client_default_limit(client):
    resp = await client.post("/api/v1/clients/", json={"name": "Default"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["storage"]["total_bytes"] == 100 * 1024 * 1024


@pytest.mark.asyncio
async def test_list_clients(client):
    await create_test_client(client, "A")
    await create_test_client(client, "B")
    resp = await client.get("/api/v1/clients/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_get_client_not_found(client):
    resp = await client.get("/api/v1/clients/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_client_name(client):
    c = await create_test_client(client, "OldName")
    resp = await client.patch(f"/api/v1/clients/{c['id']}", json={"name": "NewName"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "NewName"


@pytest.mark.asyncio
async def test_update_client_increase_limit(client):
    c = await create_test_client(client, storage_mb=10)
    resp = await client.patch(f"/api/v1/clients/{c['id']}", json={"storage_limit_mb": 50})
    assert resp.status_code == 200
    assert resp.json()["storage"]["total_mb"] == 50.0


@pytest.mark.asyncio
async def test_delete_client(client):
    c = await create_test_client(client)
    resp = await client.delete(f"/api/v1/clients/{c['id']}")
    assert resp.status_code == 204
    resp2 = await client.get(f"/api/v1/clients/{c['id']}")
    assert resp2.status_code == 404


# ── File upload tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_file_success(client):
    c = await create_test_client(client, storage_mb=10)
    resp = await upload_test_file(client, c["id"], size_bytes=1024 * 1024)  # 1 MB
    assert resp.status_code == 201
    data = resp.json()
    assert data["file"]["file_size_bytes"] == 1024 * 1024
    assert data["storage_after"]["used_bytes"] == 1024 * 1024
    assert data["storage_after"]["remaining_bytes"] == 9 * 1024 * 1024


@pytest.mark.asyncio
async def test_upload_file_quota_exceeded(client):
    c = await create_test_client(client, storage_mb=1)
    resp = await upload_test_file(client, c["id"], size_bytes=2 * 1024 * 1024)  # 2MB > 1MB
    assert resp.status_code == 507
    detail = resp.json()["detail"]
    assert detail["required_bytes"] == 2 * 1024 * 1024
    assert detail["available_bytes"] == 1 * 1024 * 1024


@pytest.mark.asyncio
async def test_upload_multiple_files_exact_quota(client):
    c = await create_test_client(client, storage_mb=2)
    half_mb = 1024 * 1024
    r1 = await upload_test_file(client, c["id"], "a.txt", half_mb)
    r2 = await upload_test_file(client, c["id"], "b.txt", half_mb)
    assert r1.status_code == 201
    assert r2.status_code == 201

    # One more byte should fail
    r3 = await upload_test_file(client, c["id"], "c.txt", 1)
    assert r3.status_code == 507


@pytest.mark.asyncio
async def test_upload_to_nonexistent_client(client):
    resp = await upload_test_file(client, "ghost-client-id")
    assert resp.status_code == 404


# ── File listing & retrieval ──────────────────────────────────────

@pytest.mark.asyncio
async def test_list_files(client):
    c = await create_test_client(client)
    await upload_test_file(client, c["id"], "x.txt", 100)
    await upload_test_file(client, c["id"], "y.txt", 200)
    resp = await client.get(f"/api/v1/clients/{c['id']}/files/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 2
    assert len(data["files"]) == 2


@pytest.mark.asyncio
async def test_get_file_metadata(client):
    c = await create_test_client(client)
    up = await upload_test_file(client, c["id"], "doc.pdf", 5000)
    file_id = up.json()["file"]["id"]

    resp = await client.get(f"/api/v1/clients/{c['id']}/files/{file_id}")
    assert resp.status_code == 200
    assert resp.json()["file_name"] == "doc.pdf"


@pytest.mark.asyncio
async def test_get_file_wrong_client(client):
    c1 = await create_test_client(client, "C1")
    c2 = await create_test_client(client, "C2")
    up = await upload_test_file(client, c1["id"])
    file_id = up.json()["file"]["id"]

    resp = await client.get(f"/api/v1/clients/{c2['id']}/files/{file_id}")
    assert resp.status_code == 403


# ── File deletion tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_file_reclaims_storage(client):
    c = await create_test_client(client, storage_mb=5)
    up = await upload_test_file(client, c["id"], "big.bin", 3 * 1024 * 1024)
    file_id = up.json()["file"]["id"]

    resp = await client.delete(f"/api/v1/clients/{c['id']}/files/{file_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["storage_after"]["used_bytes"] == 0
    assert data["storage_after"]["remaining_bytes"] == 5 * 1024 * 1024


@pytest.mark.asyncio
async def test_delete_nonexistent_file(client):
    c = await create_test_client(client)
    resp = await client.delete(f"/api/v1/clients/{c['id']}/files/fake-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_wrong_owner(client):
    c1 = await create_test_client(client, "Owner")
    c2 = await create_test_client(client, "Thief")
    up = await upload_test_file(client, c1["id"], "secret.txt", 100)
    file_id = up.json()["file"]["id"]
    resp = await client.delete(f"/api/v1/clients/{c2['id']}/files/{file_id}")
    assert resp.status_code == 403


# ── Recalculate storage ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_recalculate_storage(client):
    c = await create_test_client(client)
    await upload_test_file(client, c["id"], "a.dat", 500)
    await upload_test_file(client, c["id"], "b.dat", 300)

    resp = await client.post(f"/api/v1/clients/{c['id']}/recalculate-storage")
    assert resp.status_code == 200
    assert resp.json()["storage"]["used_bytes"] == 800


# ── Health check ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Coverage gap: client error paths ──────────────────────────────

@pytest.mark.asyncio
async def test_update_client_not_found(client):
    # clients.py line 97-98 — PATCH on missing client
    resp = await client.patch("/api/v1/clients/no-such-id", json={"name": "X"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_client_limit_below_used(client):
    # clients.py line 99-100 + client_service.py line 71-75
    # InvalidStorageLimitError → 422
    c = await create_test_client(client, storage_mb=10)
    # Upload 5 MB so used > proposed new limit
    await upload_test_file(client, c["id"], "big.bin", 5 * 1024 * 1024)
    resp = await client.patch(
        f"/api/v1/clients/{c['id']}",
        json={"storage_limit_mb": 1},   # 1 MB < 5 MB used
    )
    assert resp.status_code == 422
    assert "Delete files first" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_client_limit_exceeds_system_max(client):
    # client_service.py line 77-79 — limit > MAX_STORAGE_LIMIT_BYTES
    from app.core.config import settings
    c = await create_test_client(client, storage_mb=10)
    over_max = settings.MAX_STORAGE_LIMIT_BYTES + 1
    resp = await client.patch(
        f"/api/v1/clients/{c['id']}",
        json={"storage_limit_bytes": over_max},
    )
    assert resp.status_code == 422
    assert "Limit cannot exceed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_client_not_found(client):
    # clients.py line 117-118
    resp = await client.delete("/api/v1/clients/ghost-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_recalculate_storage_not_found(client):
    # clients.py line 136-137
    resp = await client.post("/api/v1/clients/ghost-id/recalculate-storage")
    assert resp.status_code == 404


# ── Coverage gap: file error paths ────────────────────────────────

@pytest.mark.asyncio
async def test_list_files_client_not_found(client):
    # files.py line 81-82
    resp = await client.get("/api/v1/clients/ghost-id/files/")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_file_not_found(client):
    # files.py line 99 — file_id doesn't exist at all
    c = await create_test_client(client)
    resp = await client.get(f"/api/v1/clients/{c['id']}/files/no-such-file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_file_client_not_found(client):
    # files.py line 126 (client lookup before file lookup)
    resp = await client.delete("/api/v1/clients/ghost-id/files/ghost-file")
    assert resp.status_code == 404


# ── Coverage gap: schema edge cases ───────────────────────────────

@pytest.mark.asyncio
async def test_create_client_with_bytes_param(client):
    # schemas.py — storage_limit_bytes path (no MB field)
    resp = await client.post(
        "/api/v1/clients/",
        json={"name": "BytesCo", "storage_limit_bytes": 52428800},  # 50 MB
    )
    assert resp.status_code == 201
    assert resp.json()["storage"]["total_bytes"] == 52428800


@pytest.mark.asyncio
async def test_create_client_invalid_zero_limit(client):
    # schemas.py — storage_limit_bytes <= 0 rejected by Pydantic
    resp = await client.post(
        "/api/v1/clients/",
        json={"name": "BadCo", "storage_limit_bytes": 0},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_file_zero_size_rejected(client):
    # schemas.py — file_size_bytes must be > 0
    c = await create_test_client(client)
    resp = await client.post(
        f"/api/v1/clients/{c['id']}/files/",
        json={
            "file_name": "empty.txt",
            "file_size_bytes": 0,
            "file_path": "/tmp/empty.txt",
        },
    )
    assert resp.status_code == 422


# ── Coverage gap: storage recalculation when counter is already correct ──

@pytest.mark.asyncio
async def test_recalculate_storage_no_drift(client):
    # client_service.py — branch where actual == stored (no update needed)
    c = await create_test_client(client)
    await upload_test_file(client, c["id"], "a.txt", 1000)
    # Counter is already correct; recalculate should still return valid data
    resp = await client.post(f"/api/v1/clients/{c['id']}/recalculate-storage")
    assert resp.status_code == 200
    assert resp.json()["storage"]["used_bytes"] == 1000


# ── Coverage gap: exceptions module ───────────────────────────────

def test_exception_messages():
    # exceptions.py lines 14-15 (ClientAlreadyExistsError) and line 44 (FileNotOwnedByClientError)
    from app.core.exceptions import ClientAlreadyExistsError, FileNotOwnedByClientError

    e1 = ClientAlreadyExistsError("abc")
    assert "abc" in str(e1)
    assert e1.client_id == "abc"

    e2 = FileNotOwnedByClientError("file-1", "client-9")
    assert "file-1" in str(e2)
    assert "client-9" in str(e2)


# ── Coverage gap: pagination ───────────────────────────────────────

@pytest.mark.asyncio
async def test_list_clients_pagination(client):
    for i in range(5):
        await create_test_client(client, name=f"Client{i}")
    resp = await client.get("/api/v1/clients/?skip=2&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["clients"]) == 2


@pytest.mark.asyncio
async def test_list_files_pagination(client):
    c = await create_test_client(client)
    for i in range(5):
        await upload_test_file(client, c["id"], f"file{i}.txt", 100)
    resp = await client.get(f"/api/v1/clients/{c['id']}/files/?skip=1&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_files"] == 5
    assert len(data["files"]) == 2


# ── Coverage gap: cascade delete removes files ─────────────────────

@pytest.mark.asyncio
async def test_delete_client_cascades_files(client):
    c = await create_test_client(client)
    up = await upload_test_file(client, c["id"], "orphan.txt", 500)
    file_id = up.json()["file"]["id"]

    await client.delete(f"/api/v1/clients/{c['id']}")

    # Client gone
    assert (await client.get(f"/api/v1/clients/{c['id']}")).status_code == 404
    # File also gone (file routes return 404 because client is gone)
    resp = await client.get(f"/api/v1/clients/{c['id']}/files/{file_id}")
    assert resp.status_code == 404


# ── Coverage gap: get_client success path (client_service.py:47) ──

@pytest.mark.asyncio
async def test_get_client_success(client):
    c = await create_test_client(client, name="Lookup Co", storage_mb=25)
    resp = await client.get(f"/api/v1/clients/{c['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Lookup Co"
    assert data["storage"]["total_mb"] == 25.0


# ── Coverage gap: recalculate WITH drift (client_service.py:107-110) ─

@pytest.mark.asyncio
async def test_recalculate_storage_with_drift(client):
    """Manually corrupt the counter then verify recalculate fixes it."""
    from sqlalchemy import update
    from app.models.models import Client

    c = await create_test_client(client)
    await upload_test_file(client, c["id"], "real.txt", 2000)

    # Corrupt the counter directly in the DB
    async with TestSessionLocal() as session:
        await session.execute(
            update(Client).where(Client.id == c["id"]).values(used_storage_bytes=99999)
        )
        await session.commit()

    # Sanity-check the drift is visible
    resp = await client.get(f"/api/v1/clients/{c['id']}")
    assert resp.json()["storage"]["used_bytes"] == 99999

    # Recalculate should correct it to 2000
    resp = await client.post(f"/api/v1/clients/{c['id']}/recalculate-storage")
    assert resp.status_code == 200
    assert resp.json()["storage"]["used_bytes"] == 2000


# ── Coverage gap: schema validator — create with limit > system max ──

@pytest.mark.asyncio
async def test_create_client_exceeds_max_limit(client):
    """ClientCreate validator rejects storage_limit_bytes > MAX (schemas.py:37)."""
    from app.core.config import settings
    resp = await client.post(
        "/api/v1/clients/",
        json={"name": "TooBig", "storage_limit_bytes": settings.MAX_STORAGE_LIMIT_BYTES + 1},
    )
    assert resp.status_code == 422


# ── Coverage gap: storage backend failure paths ───────────────────

@pytest.mark.asyncio
async def test_upload_backend_failure_raises_500(client):
    """file_service.py:97 — backend returns success=False → RuntimeError → 500."""
    import app.services.file_service as fsvc
    from app.storage.backend import StorageUploadResult

    original = fsvc.storage_backend

    class FailingUploadBackend:
        async def upload(self, **_):
            return StorageUploadResult(success=False, path="", message="Disk full")
        async def delete(self, **_): ...
        async def exists(self, **_): return False

    fsvc.storage_backend = FailingUploadBackend()
    try:
        c = await create_test_client(client)
        resp = await client.post(
            f"/api/v1/clients/{c['id']}/files/",
            json={"file_name": "x.txt", "file_size_bytes": 100, "file_path": "/x.txt"},
        )
        assert resp.status_code == 500
        assert "Storage backend rejected upload" in resp.json()["detail"]
    finally:
        fsvc.storage_backend = original


@pytest.mark.asyncio
async def test_delete_backend_failure_raises_500(client):
    """file_service.py:183 — backend returns success=False on delete → RuntimeError → 500."""
    import app.services.file_service as fsvc
    from app.storage.backend import StorageDeleteResult, StorageUploadResult

    original = fsvc.storage_backend

    class FailingDeleteBackend:
        async def upload(self, **_):
            return StorageUploadResult(success=True, path="/x.txt")
        async def delete(self, **_):
            return StorageDeleteResult(success=False, message="Permission denied")
        async def exists(self, **_): return True

    c = await create_test_client(client)
    up = await upload_test_file(client, c["id"], "x.txt", 100)
    file_id = up.json()["file"]["id"]

    fsvc.storage_backend = FailingDeleteBackend()
    try:
        resp = await client.delete(f"/api/v1/clients/{c['id']}/files/{file_id}")
        assert resp.status_code == 500
        assert "Storage backend rejected deletion" in resp.json()["detail"]
    finally:
        fsvc.storage_backend = original


# ── Coverage gap: model __repr__ methods (models.py:32,53) ──────────

@pytest.mark.asyncio
async def test_model_reprs(client):
    """Trigger __repr__ on both ORM models."""
    from app.models.models import Client, FileRecord
    import uuid

    c = Client(id=str(uuid.uuid4()), name="Repr Co", storage_limit_bytes=1024, used_storage_bytes=0)
    assert "Repr Co" in repr(c)
    assert "1024" in repr(c)

    f = FileRecord(id=str(uuid.uuid4()), client_id=c.id, file_name="r.txt", file_size_bytes=512, file_path="/r.txt")
    assert "r.txt" in repr(f)
    assert "512" in repr(f)


# ── Coverage gap: storage backend exists() (backend.py:81) ──────────

@pytest.mark.asyncio
async def test_mock_backend_exists():
    """MockStorageBackend.exists() is part of the interface contract — verify it returns True."""
    from app.storage.backend import MockStorageBackend
    backend = MockStorageBackend()
    result = await backend.exists(client_id="test", file_path="/some/path")
    assert result is True