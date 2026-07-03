# RabbitMQ as the ingestion message broker

We use RabbitMQ as the sole message broker for the ingestion event-driven
pipeline, not Kafka.

The pipeline's dominant pattern is **work distribution** — discrete per-Video
and per-stage jobs handed to one worker that acks on success / dead-letters on
failure — plus a per-Course **saga** coordinating cross-stage flow and a
fan-in (all Videos of a Source ready → Course ready). At platform (not
Netflix) scale, this needs per-message ack, per-stage queue isolation, and
retries with dead-lettering for long-running jobs like the Whisper fallback —
all native in RabbitMQ (ack/nack, DLX, TTL, priority, topic exchange), and
all hand-rolled on Kafka.

Kafka was rejected because its strengths — durable log retention, replay, and
high-throughput stream processing — are wasted at this scale, while its gaps
(no per-message ack, no native DLQ, partition-key ordering headaches for
per-Video fan-out) are exactly what this pipeline depends on. The only future
reason to revisit Kafka is a genuine audit/replay/streaming need, in which
case the plan is a Transactional Outbox (DB) → Debezium → Kafka beside
RabbitMQ, not replacing it.

## Considered options

- **RabbitMQ** — chose this.
- **Kafka** — rejected; overshoot for work-queue+saga workload, missing the
  ack/DLQ/isolation primitives we need.
- **Redis Streams / Postgres LISTEN-NOTIFY** — rejected; weaker durability
  and tooling for retry-heavy multi-stage work than RabbitMQ.

## Consequences

- One broker; ops stays light (single node initially, clustered if needed).
- Stage isolation = one queue per stage; long Whisper jobs cannot starve fast
  caption fetches.
- No built-in durable event store / replay — if that need emerges, introduce
  the outbox→Kafka path above rather than migrating the broker.
