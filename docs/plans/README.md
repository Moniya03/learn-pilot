# LearnPilot backend implementation plans

These plans implement the ADR-backed microservice shape. The old `api/` FastAPI scaffold is a leftover from the pre-microservices design and should be removed or restructured under `services/` during Phase 0.

## Ownership

| Area | Owner |
|---|---|
| Phase 0 infra, `shared/`, migrations runner | Moniya |
| identity-service | Moniya |
| catalog-service | Moniya |
| notes-service | Moniya |
| ingestion-service | Vasu |
| ai-service | Vasu |

## Suggested build order

1. Phase 0 infra: repo layout, Compose, Postgres schemas/users, RabbitMQ, MinIO, Weaviate, Zitadel, KrakenD, `shared/`, migrations runner.
2. identity-service: login profile sync and `User` lookup.
3. catalog-service vertical slice: create Source/Course, publish `IngestSource`, read Course/Lesson, consume ingestion events.
4. ingestion-service pipeline: resolver -> metadata -> transcript -> whisper -> embed, then `CourseReady`/`VideoReady`.
5. ai-service: Q&A, Summary cache, topic-boundary endpoint.
6. notes-service: Notebook and Note CRUD.
7. Hardening: quotas, retries, DLQs, outbox relays, CI smoke tests.

## Dependency graph

```text
web (out of scope)
  -> KrakenD (JWT validation + trusted headers)
      -> identity-service -> identity schema
      -> catalog-service  -> catalog schema
          --IngestSource command--> RabbitMQ --> ingestion-service
          <--VideosDiscovered/CourseReady events-- ingestion-service
          --topic boundary HTTP--> ai-service
      -> notes-service    -> notes schema
      -> ai-service       -> ai schema + Weaviate + LiteLLM/Groq

ingestion-service -> ingestion schema + MinIO + Weaviate + Groq Whisper + HF embeddings
identity-service  -> Zitadel sub maps to User.owner_id
all services      -> shared current_user dependency, schemas, config
```

## Phase 0

Phase 0 creates the runnable skeleton and contracts the services depend on: `docker-compose.yml`, KrakenD config, Zitadel bootstrap, `shared/`, per-service migrations, and private-network-only service bindings. No frontend implementation is planned here; only API contracts the Next.js app will consume.

## How to use these plans

Each service plan is ordered top-to-bottom with task IDs. Implement Phase 0 first, then each owned service independently. For cross-service events or HTTP contracts, the producing service owns the contract and the consuming service references it; coordinate with the other owner before changing payloads.

## Open assumptions carried across plans

- IDs are UUIDs except `owner_id`, which is the Zitadel `sub` string.
- Public API routes are exposed by KrakenD under `/api/<service>/*`; internal service paths stay `/v1/*`.
- Python stack: FastAPI + Pydantic v2 + SQLAlchemy/Alembic or SQLModel + asyncpg; exact ORM is left to implementation.
- Quota limits are config values; enforcement points are fixed in plans.
