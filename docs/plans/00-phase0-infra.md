Owner: Moniya

# Phase 0 infra plan

Purpose: create the runnable backend skeleton, shared contracts, gateway auth, and databases for the 5 FastAPI services.

Dependencies: ADR-0008..0013; coordinate with Vasu on ingestion/ai env vars and RabbitMQ contracts.

## Repository layout

Restructure to ADR-0013:

```text
services/{identity,catalog,notes,ingestion,ai}/
shared/
gateway/krakend.json
infra/docker-compose.yml
infra/migrations/run.py
web/  # existing/future frontend; not implemented here
```

Remove or migrate the old `api/` scaffold; it must not remain the active backend entrypoint.

## docker-compose.yml

Services on one private network `learnpilot_private`; only `gateway` and `web` publish public ports.

| service | image/build | ports | volumes/env |
|---|---|---|---|
| postgres | `postgres:16` | private only | DBs: `learnpilot`, `zitadel`; users per schema |
| rabbitmq | `rabbitmq:3-management` | private; optional admin localhost only | topic exchange + DLX/DLQ definitions |
| weaviate | official standalone | private only | one collection `TranscriptChunk`, vectors 1024-dim |
| minio | `minio/minio` | private; optional console localhost only | buckets `audio`, `captions`, `transcripts` |
| zitadel | official Zitadel | private via KrakenD/web callback | shared Postgres `zitadel` DB |
| gateway | KrakenD | public `:8080` | `gateway/krakend.json` |
| identity/catalog/notes/ingestion/ai | local Dockerfiles | private only | schema-scoped DB URL, shared env |
| web | Next.js | public `:3000` | consumes Zitadel OIDC + KrakenD |

Bind FastAPI apps to `0.0.0.0` inside Docker but do not publish their ports.

## KrakenD config

`gateway/krakend.json` must:

- validate Zitadel JWT at edge using Zitadel JWKS;
- enforce audience/client ID and expiry;
- strip inbound `X-User-Id`, `X-User-Email`, `X-Auth-Scopes` before auth;
- inject trusted headers to backends:
  - `X-User-Id`: JWT `sub`
  - `X-User-Email`: JWT email claim
  - `X-Auth-Scopes`: scopes/roles joined by spaces
- expose public prefixes:
  - `/api/identity/*` -> `identity-service:8000/v1/*`
  - `/api/catalog/*` -> `catalog-service:8000/v1/*`
  - `/api/notes/*` -> `notes-service:8000/v1/*`
  - `/api/ingestion/*` -> `ingestion-service:8000/v1/*` (admin/debug only)
  - `/api/ai/*` -> `ai-service:8000/v1/*`

All backend routes require auth except `/healthz`.

## Zitadel bootstrap

Script `infra/zitadel/bootstrap.*` creates:

- organization/project: `learnpilot`;
- Google identity provider configured from env (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`);
- OIDC app for NextAuth/web callback;
- API audience/client for KrakenD JWT validation;
- initial admin user from env;
- exported issuer URL and JWKS URL for KrakenD.

identity-service stores no tokens; it only stores the Zitadel `sub` as `User.owner_id`.

## shared/ package

Keep `shared/` contract-only:

```text
shared/
  auth.py              # FastAPI current_user header dependency
  config.py            # pydantic-settings common env
  events.py            # RabbitMQ BaseEvent/BaseCommand + payloads
  quotas.py            # quota keys/helpers, no business writes
  litellm_client.py    # Groq-through-LiteLLM adapter
  embeddings.py        # HF e5 adapter; query:/passage: helpers
  weaviate.py          # TranscriptChunk collection helpers
  minio.py             # bucket/key/checksum helpers
  outbox.py            # relay base loop helpers
```

Core Pydantic contracts:

```python
class CurrentUser(BaseModel):
    owner_id: str
    email: EmailStr | None = None
    scopes: set[str] = set()

class Envelope(BaseModel):
    schema_version: Literal[1] = 1
    saga_id: UUID
    course_id: UUID
    video_id: UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any]

class BaseEvent(Envelope):
    event_id: UUID
    event_type: str

class BaseCommand(Envelope):
    command_id: UUID
    command_type: str
```

Contract payloads live here but are owned by producers:

- catalog owns `IngestSourceCommand`;
- ingestion owns `VideosDiscovered`, `CourseReady`, `VideoReady`;
- ai owns AI cache/summary events if added later.

## RabbitMQ topology

One topic exchange: `learnpilot.topic`.

Common args per worker queue:

- `x-dead-letter-exchange=learnpilot.dlx`
- retry queue with `x-message-ttl` then dead-letter back to main routing key
- terminal DLQ routing to `learnpilot.dlq.<queue>`

Initial queues:

| queue | routing key | owner |
|---|---|---|
| `catalog.commands.ingest_source` | `catalog.command.ingest_source` | Vasu consumes, Moniya publishes |
| `ingestion.stage.resolver` | `ingestion.stage.resolver` | Vasu |
| `ingestion.stage.metadata` | `ingestion.stage.metadata` | Vasu |
| `ingestion.stage.transcript` | `ingestion.stage.transcript` | Vasu |
| `ingestion.stage.whisper` | `ingestion.stage.whisper` | Vasu |
| `ingestion.stage.embed` | `ingestion.stage.embed` | Vasu |
| `catalog.events.videos_discovered` | `ingestion.event.videos_discovered` | Moniya |
| `catalog.events.course_ready` | `ingestion.event.course_ready` | Moniya |
| `ai.events.video_ready` | `ingestion.event.video_ready` | Vasu publishes, Vasu consumes in ai |

## Migrations runner

**Deviation (2026-07-04):** instead of a centralized `infra/migrations/run.py`,
we use a per-service `migrate.py` that runs on container startup (`sh -c "uv run
python -m migrate && uvicorn..."`). Each service:

- creates its own schema `if not exists` on startup;
- applies `services/<name>/migrations/*.sql` in sorted order;
- uses idempotent DDL (`create if not exists`).

The per-service approach is simpler (no extra orchestration binary, no cross-schema
grants to configure), and container restarts are cheap for local dev. A centralized
runner can still be added later if you want one-command `migrate all` — it would just
shell out to each service's `migrate.py`.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| INF-1 | Restructure repo | - | S | `services/`, `shared/`, `gateway/`, `infra/` exist; old `api/` removed or marked migrated. |
| INF-2 | Compose skeleton | INF-1 | M | `docker compose up` starts Postgres/RabbitMQ/Weaviate/MinIO/Zitadel/gateway/5 services. |
| INF-3 | Postgres schemas/migrations | INF-2 | S | `identity` schema created, migrations apply in Docker entrypoint; `asyncpg` pool scoped with `search_path`. Other 4 services adopt the same pattern when built. (**Deviation:** per-service `migrate.py` replaces centralized runner; DB roles deferred as YAGNI.) |
| INF-4 | RabbitMQ definitions | INF-2 | M | exchange, queues, retry queues, DLX/DLQs created by config/script. |
| INF-5 | MinIO + Weaviate bootstrap | INF-2 | M | buckets and `TranscriptChunk` collection created idempotently. |
| INF-6 | Zitadel bootstrap | INF-2 | L | Google IdP, OIDC clients, JWKS URL, admin user configured from env. |
| INF-7 | KrakenD auth routes | INF-6 | L | JWT validation works; forged inbound trusted headers are stripped. |
| INF-8 | `shared/` package | INF-3 | M | current_user dependency and base command/event/client adapters import in all services. |
| INF-9 | Health/CI smoke | INF-2..8 | S | one command verifies only gateway/web expose ports and all `/healthz` pass. |

## Cross-service dependencies

- Coordinate RabbitMQ payload class names with Vasu before services consume/publish.
- Coordinate KrakenD public route prefixes with both owners before client work starts.

## Open questions / assumptions

- Exact Zitadel bootstrap tooling can be Terraform, API script, or `zitadel` CLI; choose the shortest reliable path.
- Whether RabbitMQ management and MinIO console are exposed on localhost in dev only is an ops choice.
