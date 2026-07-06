"""catalog-service — owns Source, Video, Course, Lesson, Plan, Day, Progress.

Routes (all behind KrakenD except /healthz):
  GET   /healthz   DB connectivity check
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

import messaging
from fastapi import FastAPI
from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal, engine
from routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await messaging.startup(app)
    try:
        yield
    finally:
        await messaging.shutdown(app)
        await engine.dispose()


app = FastAPI(title="catalog-service", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict:
    """DB connectivity check; no auth."""
    async with AsyncSessionLocal() as s:
        val = (await s.execute(text("select 1"))).scalar()
    return {"status": "ok", "db": val == 1, "schema": settings.DB_SCHEMA}