# Monorepo layout under microservices

The repo is a single monorepo containing all services + shared code + the
frontend, structured per the microservices boundaries (ADR-0008). This
supersedes the earlier informal `api/ workers/ shared/ web/` layout assumed
when a single FastAPI service was planned.

## Layout

```
/
├── CONTEXT.md
├── docs/adr/
├── services/
│   ├── identity/      # identity-service (FastAPI)
│   ├── catalog/       # catalog-service (FastAPI)
│   ├── notes/         # notes-service (FastAPI)
│   ├── ingestion/     # ingestion-service (FastAPI) + stage workers as subpackages
│   └── ai/            # ai-service (FastAPI)
├── shared/            # cross-service: Pydantic event/command schemas, KrakenD header dependency, LLM/embed clients, common config
├── web/               # Next.js (App Router, TS) — next-auth against Zitadel
├── gateway/           # KrakenD config (krakend.json)
├── infra/             # docker-compose.yml, Zitadel bootstrap, migrations runner
└── README.md
```

## Why one monorepo

- Cross-service contracts (event/command schemas in `shared/`) change
  together; a single repo keeps producer and consumer of an event in one
  atomic commit, avoiding the version-skew drift of polyrepo microservices.
- One venv/dependency set for the Python services; one CI; one history.
- Each service is still independently deployable (separate Docker image built
  from its `services/<name>/` directory).

## Consequences

- `shared/` is a dependency of every service; changes there ripple — keep it
  to genuine contracts (event/command schemas, header dependency, common
  clients), not business logic.
- ingestion-service's stage workers live as subpackages of `services/ingestion/`,
  not a separate top-level dir — they share the ingestion schema + saga code.
- Per-service migrations live under `services/<name>/migrations/` and run
  against that service's schema only.
- KrakenD config + Compose live under `gateway/` and `infra/`, versioned
  alongside the code they deploy.

## Considered options

- **Single monorepo, per-service dirs** — chose this.
- **Polyrepo (one repo per service)** — rejected; contract version-skew and
  cross-cutting change friction at solo scale.
- **Single package, multi-process (pre-microservices)** — superseded by
  ADR-0008.
