"""catalog-service — owns Source, Video, Course, Lesson, Plan, Day, Progress.

Routes (all behind KrakenD except /healthz):
  GET   /healthz   DB connectivity check
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="catalog-service", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    """DB connectivity check; no auth."""
    async with AsyncSessionLocal() as s:
        val = (await s.execute(text("select 1"))).scalar()
    return {"status": "ok", "db": val == 1, "schema": settings.DB_SCHEMA}