"""Plan worker (CAT-6): polls for pending auto plans, calls ai-service.

Run as a background task in main.py lifespan; gated on PLAN_WORKER_ENABLED.
One plan failure must not stop the worker loop.
"""
import asyncio
import logging

from db.session import AsyncSessionLocal
from plan_service import generate_plan, list_pending_auto_plans

_log = logging.getLogger(__name__)


async def run_plan_worker(poll_interval: float = 3.0) -> None:
    """Forever loop. Per-plan and per-cycle exceptions are caught and logged."""
    _log.info("plan worker started; polling every %.1fs", poll_interval)
    while True:
        try:
            async with AsyncSessionLocal() as s:
                plans = await list_pending_auto_plans(s)
            for plan in plans:
                try:
                    await generate_plan(plan.id)
                except Exception as e:  # ponytail: isolate per-plan failures
                    _log.exception("generate_plan failed for %s: %s", plan.id, e)
        except Exception as e:
            _log.exception("plan worker poll cycle failed: %s", e)
        await asyncio.sleep(poll_interval)
