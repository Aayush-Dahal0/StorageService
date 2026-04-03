from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import init_db
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB tables on startup."""
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="""
## File Storage Quota Management Service

Manage client storage quotas and file metadata with strict quota enforcement.

### Key features
- **Quota-gated uploads** — files are rejected before they land if quota is insufficient.
- **Atomic operations** — quota counters and file records update in the same DB transaction.
- **Concurrency-safe** — client rows are locked with `SELECT … FOR UPDATE` during writes.
- **Pluggable storage backend** — swap `MockStorageBackend` for S3, GCS, etc. with zero service-layer changes.
- **Drift repair** — `/recalculate-storage` recomputes quota from actual file records.
        """,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS (adjust origins for production) ─────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Global exception handlers ────────────────────────────────
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )

    # ── Routers ──────────────────────────────────────────────────
    app.include_router(api_router, prefix="/api/v1")

    # ── Health check ─────────────────────────────────────────────
    @app.get("/health", tags=["Health"], summary="Health check")
    async def health():
        return {"status": "ok", "service": settings.APP_NAME, "version": settings.APP_VERSION}

    return app


app = create_app()
