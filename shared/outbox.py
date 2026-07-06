"""Framework-agnostic outbox relay loop.

Plug in a DB fetcher and an AMQP publisher; this loop just polls,
publishes, and marks rows published. Runs forever until cancelled.
"""
import asyncio
import logging

_log = logging.getLogger(__name__)

async def run_outbox_relay(
    *,
    fetch_unpublished,   # async () -> list[dict]   rows: {id, routing_key, message}
    publish,             # async (routing_key: str, message: dict) -> None
    mark_published,      # async (row_id) -> None
    poll_interval: float = 1.0,
    batch_size: int = 100,
    logger=None,
) -> None:
    log = logger or _log
    while True:
        try:
            rows = (await fetch_unpublished())[:batch_size]
        except Exception as exc:  # ponytail: log + backoff, never crash the relay
            log.exception("outbox fetch failed: %s", exc)
            await asyncio.sleep(poll_interval)
            continue
        for row in rows:
            try:
                await publish(row["routing_key"], row["message"])
            except Exception as exc:
                log.exception("outbox publish failed for row %s: %s", row.get("id"), exc)
                continue
            try:
                await mark_published(row["id"])
            except Exception as exc:
                log.exception("outbox mark_published failed for row %s: %s", row.get("id"), exc)
        if not rows:
            await asyncio.sleep(poll_interval)
