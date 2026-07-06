"""Outbox relay for catalog-service: drain `catalog.outbox` to RabbitMQ.

Wraps `shared.outbox.run_outbox_relay` with SQLAlchemy async fetchers.
The relay uses its own short-lived sessions (not the request-scoped
`get_session`) so it runs independent of HTTP traffic.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import AsyncSessionLocal
from models import Outbox
import shared.outbox
import shared.rabbit


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_unpublished() -> list[dict[str, Any]]:
    """Return up to 100 oldest unpublished outbox rows."""
    async with AsyncSessionLocal() as s:  # type: AsyncSession
        rows = (
            await s.execute(
                select(Outbox)
                .where(Outbox.published_at.is_(None))
                .order_by(Outbox.created_at)
                .limit(100)
            )
        ).scalars().all()
        return [
            {"id": r.id, "routing_key": r.routing_key, "message": r.message}
            for r in rows
        ]


async def mark_published(row_id) -> None:
    """Stamp published_at on a single outbox row in its own session."""
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Outbox).where(Outbox.id == row_id).values(published_at=_now())
        )
        await s.commit()


async def start_relay(channel, poll_interval: float = 1.0) -> asyncio.Task:
    """Start the relay loop as a background asyncio task; return the task."""
    return asyncio.create_task(
        shared.outbox.run_outbox_relay(
            fetch_unpublished=fetch_unpublished,
            publish=lambda rk, msg: shared.rabbit.publish(channel, rk, msg),
            mark_published=mark_published,
            poll_interval=poll_interval,
        )
    )
