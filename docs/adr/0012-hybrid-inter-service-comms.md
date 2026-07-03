# Hybrid inter-service communication: sync HTTP + async events

Services communicate two ways, chosen per flow by latency tolerance:

- **Synchronous HTTP via KrakenD** for client-facing and on-demand
  service-to-service calls that need a result now:
  - Q&A (streaming answer to the client), summarize (on-demand), course/
    lesson/plan/notes/progress CRUD, login.
  - catalog-service asking ai-service to generate topic boundaries for an
    auto-split Plan (single LLM call; slow-ish but the caller waits).
- **Asynchronous RabbitMQ events** for durable pipeline hand-offs where a
  downstream service being down must not block the producer:
  - ingestion → catalog: `VideosDiscovered` (create Lesson rows),
    `CourseReady` (mark Course ready).
  - ingestion → ai: `VideoReady` (this Video's transcript + embeddings are
    available; ai-service may summarize on demand later).

## Why hybrid

- Client-facing calls fundamentally need an answer in the request cycle;
  forcing them through async events + response subscriptions would
  over-engineer the UX (streaming Q&A over a request-response event channel
  is awkward).
- Pipeline hand-offs fundamentally need durability + decoupling — the reason
  RabbitMQ was chosen (ADR-0001). Making them sync would let a downstream
  service outage block ingestion stages and lose the durability RabbitMQ
  buys.

## Transactional outbox for event publication

When a service commits a DB change that should also publish an event, it
writes the event into an `outbox` table in its own schema **in the same
transaction** as the change. A outbox-relay process reads the outbox and
publishes to RabbitMQ, then marks the row published. This makes DB commit +
event publish atomic and avoids the dual-write problem (commit succeeds,
publish fails → lost event; or publish succeeds, commit fails → phantom
event).

## Consequences

- Two integration modes in the codebase; each call site declares which it is.
- Outbox tables + a relay process per service that publishes events.
- Inter-service sync calls go through KrakenD on the private network (with
  trusted headers propagated, ADR-0010); they are not direct service-to-
  service HTTP over the Docker network except where KrakenD routing is
  bypassed intentionally (decision deferred to implementation).
- Event consumers are idempotent (ADR-0001's dedupe applies on the event
  side too; events carry an `event_id`).

## Considered options

- **Hybrid sync + async** — chose this.
- **All sync HTTP via KrakenD** — rejected; pipeline hand-offs lose
  durability/decoupling, downstream outages block ingestion.
- **All async events (incl. client-facing via response subscriptions)** —
  rejected; over-engineers client-facing latency/streaming UX.
