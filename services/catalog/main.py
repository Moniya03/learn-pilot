"""catalog-service — owns Source, Video, Course, Lesson, Plan, Day, Progress.

Routes (all behind KrakenD except /healthz):
  GET   /healthz   DB connectivity check
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

import messaging
import plan_worker
from config import settings
from db.session import AsyncSessionLocal, engine
from plan_routes import router as plan_router
from routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await messaging.startup(app)
    plan_task: asyncio.Task | None = None
    if settings.PLAN_WORKER_ENABLED:
        plan_task = asyncio.create_task(plan_worker.run_plan_worker())
        app.state.plan_task = plan_task
    try:
        yield
    finally:
        if plan_task is not None:
            plan_task.cancel()
            try:
                await plan_task
            except Exception:  # ponytail: cancellation; don't re-raise
                pass
            if hasattr(app.state, "plan_task"):
                delattr(app.state, "plan_task")
        await messaging.shutdown(app)
        await engine.dispose()


app = FastAPI(title="catalog-service", lifespan=lifespan)
app.include_router(router)
app.include_router(plan_router)


@app.get("/healthz")
async def healthz() -> dict:
    """DB connectivity check; no auth."""
    async with AsyncSessionLocal() as s:
        val = (await s.execute(text("select 1"))).scalar()
    return {"status": "ok", "db": val == 1, "schema": settings.DB_SCHEMA}
