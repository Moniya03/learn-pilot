"""Common service settings shared across all FastAPI services.

Each service subclasses this and adds its own DB_SCHEMA default.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    DB_SCHEMA: str
    RABBITMQ_URL: str | None = None

    @property
    def dsn(self) -> str:
        """asyncpg-friendly DSN (strip the SQLAlchemy +asyncpg driver token)."""
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")