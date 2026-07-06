"""Async RabbitMQ helpers: connection, topology, queue-with-retry, publisher.

Services declare their own queues via `declare_queue(...)`. Topology helpers
are framework-agnostic — pass any `aio_pika.Channel` in.
"""
import json
import os

import aio_pika


RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL", "amqp://learnpilot:learnpilot@rabbitmq:5672/%2F"
)
EXCHANGE = "learnpilot.topic"
DLX = "learnpilot.dlx"


async def connect() -> aio_pika.RobustConnection:
    """Open a robust connection (auto-reconnects on broker blips)."""
    return await aio_pika.connect_robust(RABBITMQ_URL)


async def declare_topology(channel: aio_pika.Channel) -> tuple[aio_pika.Exchange, aio_pika.Exchange]:
    """Ensure the topic exchange and DLX exist. Idempotent; call on startup."""
    main = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange(DLX, aio_pika.ExchangeType.TOPIC, durable=True)
    return main, dlx


async def declare_queue(
    channel: aio_pika.Channel,
    queue_name: str,
    routing_key: str,
    *,
    dlq_name: str | None = None,
    retry_ttl_ms: int = 60000,
) -> aio_pika.Queue:
    """Declare a worker queue with a retry-via-DLX loop and a terminal DLQ.

    Returns the main queue (bind your consumer to it).
    """
    # Main: dead-letters to DLX, preserving the original routing key.
    main = await channel.declare_queue(
        queue_name,
        durable=True,
        arguments={"x-dead-letter-exchange": DLX},
    )
    await main.bind(EXCHANGE, routing_key=routing_key)

    # Retry: holds for retry_ttl_ms, then dead-letters back to the main
    # exchange under the original routing key (re-enters the main queue).
    retry = await channel.declare_queue(
        f"{queue_name}.retry",
        durable=True,
        arguments={
            "x-message-ttl": retry_ttl_ms,
            "x-dead-letter-exchange": EXCHANGE,
            "x-dead-letter-routing-key": routing_key,
        },
    )
    await retry.bind(DLX, routing_key=routing_key)

    # Terminal DLQ. Consumers explicitly publish here via DLX with this
    # routing key after exhausting retries.
    dlq_name = dlq_name or f"learnpilot.dlq.{queue_name}"
    dlq = await channel.declare_queue(dlq_name, durable=True)
    await dlq.bind(DLX, routing_key=dlq_name)
    return main


async def publish(
    channel: aio_pika.Channel,
    routing_key: str,
    body: bytes | dict,
) -> None:
    """Publish to `learnpilot.topic` with PERSISTENT delivery."""
    data = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    await exchange.publish(
        aio_pika.Message(data, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=routing_key,
    )
