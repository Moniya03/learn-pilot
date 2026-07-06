"""catalog-service settings. DB_SCHEMA is fixed to `catalog`."""
from shared.config import Settings as BaseSettings


class CatalogSettings(BaseSettings):
    DB_SCHEMA: str = "catalog"
    RABBITMQ_URL: str | None = None
    AI_SERVICE_URL: str = "http://ai-service:8000"
    COURSE_CREATE_DAILY_LIMIT: int = 50  # ponytail: quota pre-check default; product config moves to shared later
    PLAN_WORKER_ENABLED: bool = True  # ponytail: in-process plan worker; turn off in tests/workers-only deploys

    @property
    def async_dsn(self) -> str:
        """SQLAlchemy async DSN (keeps +asyncpg driver)."""
        if "postgresql+asyncpg://" in self.DATABASE_URL:
            return self.DATABASE_URL
        return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")


settings = CatalogSettings()  # type: ignore[call-arg]
