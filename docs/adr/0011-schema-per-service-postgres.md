# Schema-per-service on shared Postgres

The relational store is **one Postgres instance, schema-per-service**:
`identity`, `catalog`, `notes`, `ingestion`, `ai`. Each service connects only
to its own schema. No cross-schema joins. Service-to-service data needs go via
API call or async event, never a shared table. Keycloak—n/a; Zitadel gets its
own DB on the same Postgres instance.

## Why schema-per-service

- **Data ownership without database-per-service ops.** Each service owns its
  tables; another service can't reach in and read/write them. Ownership is
  enforced at the schema + connection-grant level, not just convention.
- **One instance to back up, upgrade, tune** — pragmatic for single-VPS scale,
  where running six separate Postgres instances would dominate RAM/ops for no
  real isolation gain yet.
- **Clean extraction path.** If a service later needs its own DB (e.g.
  ingestion or progress scaling out), lifting one schema to its own instance
  is mechanical — the service already treats its schema as the only store and
  already exposes data only via API/event.

## Consequences

- Each service's DB client is configured with a user scoped to its schema
  only; migrations run per-service against its schema.
- Shared reference data (e.g. a `User` row referenced by `owner_id` across
  schemas) is **referenced by id, never joined** — catalog/notes/ingestion/ai
  store `owner_id` but resolve User details via identity-service.
- Weaviate (embeddings) and MinIO (artifacts) are shared infrastructure like
  RabbitMQ, not owned by one schema — ingestion writes them, ai reads them;
  this is covered by ADR-0002 and ADR-0005.
- One Postgres is a single point of failure; acceptable at this scale, backed
  up as one unit.

## Considered options

- **Shared Postgres, schema-per-service** — chose this.
- **Database-per-service** — rejected at this scale; 6 DB instances on one
  VPS is ops-heavy for solo deployment with no isolation payoff yet.
- **Shared DB, shared schema** — rejected; no data ownership, services
  coupled at the table level, defeats the microservices boundary.
