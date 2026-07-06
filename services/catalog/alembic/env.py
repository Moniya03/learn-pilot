"""Alembic env for catalog-service.

Async-aware. The DSN is read from catalog.config.settings (not alembic.ini)
so the same config the FastAPI app uses drives migrations.
"""
import asyncio
import os
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# /app/<here>/env.py -> /app is the project root (where models.py, config.py, main.py live).
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from config import settings  # noqa: E402  (sys.path tweaked above)
from models import Base  # noqa: E402

config = context.config

# Hand the DSN to alembic via its standard config attribute.
config.set_main_option("sqlalchemy.url", settings.async_dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL to stdout without a live DB connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect to the DB and apply migrations asynchronously."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
