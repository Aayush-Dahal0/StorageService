from fastapi import APIRouter
from app.api.routes import clients, files

api_router = APIRouter()
api_router.include_router(clients.router)
api_router.include_router(files.router)
