"""SQLAlchemy 2.x async engine scoped to the `catalog` schema via search_path."""
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings

# search_path is set on every new connection via asyncpg's server_settings
# (cleanest async pattern; no connect-listener boilerplate).
engine = create_async_engine(
    settings.async_dsn,
    pool_size=5,
    max_overflow=5,
    connect_args={"server_settings": {"search_path": settings.DB_SCHEMA}},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session with the catalog search_path active."""
    async with AsyncSessionLocal() as session:
        yield session
