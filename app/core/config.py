from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "File Storage Quota Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    DATABASE_URL: str = "sqlite+aiosqlite:///./storage_service.db"

    # Default storage limits in bytes
    DEFAULT_STORAGE_LIMIT_BYTES: int = 100 * 1024 * 1024  # 100MB
    MAX_STORAGE_LIMIT_BYTES: int = 10 * 1024 * 1024 * 1024  # 10GB

    model_config = {"env_file": ".env"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
