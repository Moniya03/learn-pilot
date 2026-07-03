# Microservices architecture with KrakenD gateway

LearnPilot is built as **microservices from day one**, fronted by a KrakenD
API gateway, with all services on a private Docker Compose network.

This supersedes the implicit single-API shape assumed by earlier ADRs and the
"no gateway" stance in ADR-0006 (now superseded).

## Services (bounded contexts)

- **identity-service** — User profile, find-or-create on login. Owns the
  `identity` schema.
- **catalog-service** — Source, Course, Lesson, Plan, Day, Progress (Progress
  stays here; it is tightly coupled to Lesson). Owns the `catalog` schema.
- **notes-service** — Notebook + Note CRUD. Owns the `notes` schema.
- **ingestion-service** — IngestionSaga + the resolver/metadata/transcript/
  whisper/embed stage workers; writes to MinIO (artifacts) and Weaviate
  (embeddings); owns the `ingestion` schema (saga + video/transcript cache
  rows).
- **ai-service** — summarize, Q&A, topic-boundary generation; reads Weaviate
  + calls the LLM; owns the `ai` schema (summary cache).

## Why microservices from day one

Accepted ops cost in exchange for: independent deploy per bounded context,
clear data ownership, isolation of the heavy ingestion/AI paths from the
client-facing CRUD, and a shape that won't need a painful monolith→services
extraction later. The trade-off acknowledged: more services, more inter-
service calls, more ops surface on a single VPS than a modular monolith.

## Consequences

- Inter-service communication is **hybrid** (ADR-0011 will pin this): sync
  HTTP via KrakenD for client-facing + on-demand calls; async RabbitMQ events
  for pipeline hand-offs.
- Data ownership is **schema-per-service** on one shared Postgres (see
  ADR-0008 data strategy, to be written) — no cross-schema joins; service-to-
  service data needs go via API call or event, never a shared table.
- KrakenD is the sole public entry point; internal services bind only to the
  private Docker network and never expose ports publicly (ADR-0010).
- Keycloak was considered and rejected as the IdP in favor of Zitadel
  (ADR-0009) on footprint/ops grounds.

## Considered options

- **Microservices from day one** — chose this.
- **Modular monolith first** — rejected by the user; preferred the infra-grade
  shape upfront.
- **Hybrid (core monolith + ingestion/ai split)** — rejected; user wanted the
  full split now.
