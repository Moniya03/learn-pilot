"""Run identity migrations on startup; creates schema if needed.

Idempotent: uses `if not exists` throughout.
"""
import asyncio
import pathlib

import asyncpg

from config import settings

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"


async def main() -> None:
    conn = await asyncpg.connect(dsn=settings.dsn)
    try:
        await conn.execute(f'create schema if not exists "{settings.DB_SCHEMA}"')
        await conn.execute(f'set search_path to "{settings.DB_SCHEMA}"')
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            print(f"identity: applying {sql_file.name}")
            await conn.execute(sql_file.read_text())
        print("identity: migrations done")
    finally:
        await conn.close()


# ponytail: self-check — `uv run python -m migrate` applies migrations.
if __name__ == "__main__":
    asyncio.run(main())