"""Messaging lifecycle for catalog-service: connect, declare, start workers.

Kept thin — `startup()` and `shutdown()` are called from main.py's lifespan.
If `settings.RABBITMQ_URL` is None, the REST API still works and outbox
rows accumulate; a broker-less service is useful for tests.
"""
import logging

from fastapi import FastAPI

from config import settings
from consumers import (
    DLQ_COURSE_READY,
    DLQ_VIDEOS_DISCOVERED,
    QUEUE_COURSE_READY,
    QUEUE_VIDEOS_DISCOVERED,
    RK_COURSE_READY,
    RK_VIDEOS_DISCOVERED,
    on_course_ready,
    on_videos_discovered,
)
import outbox_relay
import shared.rabbit


_log = logging.getLogger(__name__)


async def startup(app: FastAPI) -> None:
    """Connect to RabbitMQ, declare topology + queues, start relay + consumers."""
    if settings.RABBITMQ_URL is None:
        _log.warning("RABBITMQ_URL unset; messaging disabled (REST still works).")
        return

    connection = await shared.rabbit.connect()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    await shared.rabbit.declare_topology(channel)

    q_videos = await shared.rabbit.declare_queue(
        channel, QUEUE_VIDEOS_DISCOVERED, RK_VIDEOS_DISCOVERED, dlq_name=DLQ_VIDEOS_DISCOVERED
    )
    q_course = await shared.rabbit.declare_queue(
        channel, QUEUE_COURSE_READY, RK_COURSE_READY, dlq_name=DLQ_COURSE_READY
    )

    relay_task = await outbox_relay.start_relay(channel)
    await q_videos.consume(on_videos_discovered)
    await q_course.consume(on_course_ready)

    app.state.connection = connection
    app.state.channel = channel
    app.state.relay_task = relay_task
    _log.info("catalog messaging started: relay + 2 consumers")


async def shutdown(app: FastAPI) -> None:
    """Cancel relay task; close channel and connection. Tolerates None/missing."""
    relay_task = getattr(app.state, "relay_task", None)
    if relay_task is not None:
        relay_task.cancel()
        try:
            await relay_task
        except Exception:  # ponytail: cancellation; don't re-raise
            pass

    channel = getattr(app.state, "channel", None)
    if channel is not None:
        try:
            await channel.close()
        except Exception:
            pass

    connection = getattr(app.state, "connection", None)
    if connection is not None:
        try:
            await connection.close()
        except Exception:
            pass

    for attr in ("relay_task", "channel", "connection"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
    _log.info("catalog messaging shut down")
