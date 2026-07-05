"""asyncpg connection pool, scoped to the identity schema via search_path."""
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from config import settings


async def pool_factory() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.dsn,
        min_size=1,
        max_size=5,
        server_settings={"search_path": settings.DB_SCHEMA},
    )


@asynccontextmanager
async def db_conn(pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    async with pool.acquire() as conn:
        yield conn