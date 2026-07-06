# catalog-service — what it is and how it works

> Owner: Moniya. Source of truth: `services/catalog/`. Spec: `docs/plans/02-catalog-service.md`.
> This document explains the service's role, design, data model, endpoints,
> messaging, and worker, then reproduces the **entire current source** so it can
> be read end-to-end without opening the repo.

## 1. Role

catalog-service is the **content and learning-state backbone** of LearnPilot.
It owns everything between "a User pastes a YouTube URL" and "a User watches
lessons and tracks progress":

- It turns a pasted URL into a **Source** and a stub **Course** (state
  `pending_ingestion`), and asks ingestion-service to go fetch the video(s).
- It receives **VideosDiscovered** / **CourseReady** events back from
  ingestion and materializes **Videos** and whole-video **Lessons**.
- It exposes the read paths the player UI needs: list/get courses, list
  lessons, and per-User **Progress** as a 0–100% figure.
- It turns a Course into a **Plan** of **Days** of Lessons — manually, or
  asynchronously by asking ai-service to draw natural topic boundaries.

It does **not** fetch video data itself (ingestion-service), transcribe
(ai-service), or store notes (notes-service). It is the structural glue and
the single home of the User'sCourses/Lessons/Plans/Progress.

### Why a separate service
Content structuring (Source→Course→Lesson→Plan→Day) and learning state
(Progress) change at a different rate and have different scaling
characteristics than the heavy AI/transcript pipeline. Splitting them lets
catalog stay a fast, mostly-relational CRUD service while ingestion/ai
elastically scale on their own. The split also enforces the trust boundary:
KrakenD validates the JWT and injects `X-User-Id`; catalog never sees a token
(ADR-0010) and only trusts the header.

## 2. Place in the system

```
                                  KrakenD (JWT -> X-User-Id)
                                     │  /api/catalog/*
                                     ▼
                          ┌──────────────────────┐
   User ──HTTP──►  web ──►│  catalog-service     │  SQLAlchemy 2.x async
                          │  (this service)      │  ──────────► catalog schema (Postgres)
                          └──────────┬───────────┘
            IngestSource command     │         topic-boundaries HTTP
            (outbox -> RabbitMQ)     │
                ▼                     ▼
              ingestion-service      ai-service
                │   │                 ▲
   VideosDiscovered  CourseReady      │ topic-boundaries HTTP (plan worker)
   events back ───► catalog consumers │
                                       plan worker calls ai-service
```

- **Out** to ingestion: a `IngestSource` command, written to `catalog.outbox`
  in the same transaction as the Source+Course, drained by a relay to
  RabbitMQ (`catalog.command.ingest_source`).
- **In** from ingestion: `VideosDiscovered` and `CourseReady` events on
  `ingestion.event.*`, consumed with `message_dedupe` dedupe.
- **Out** to ai-service: the plan worker makes a plain HTTP call to
  `POST /v1/topic-boundaries` per video (no broker involved) to get natural
  Lesson boundaries.
- **Shared** contracts (`shared/events.py`, `shared/rabbit.py`,
  `shared/outbox.py`, `shared/auth.py`, `shared/config.py`) keep these
  integrations consistent across services — catalog reads them, never
  redefines them.

## 3. Technology choices

- **SQLAlchemy 2.x async + Alembic** for data access (distinct from
  identity-service, which uses raw asyncpg). Catalog's schema has many
  joins (Course→Lesson→Progress, Plan→Day→DayLesson→Lesson) and eager
  `progress_percent` computation; the ORM pays for itself here. Decision
  recorded in the catalog plan.
- **Pydantic v2** for request/response models with `from_attributes=True`
  so ORM rows serialize directly; literals mirror the Postgres enum values.
- **asyncpg** under SQLAlchemy, with `search_path=catalog` set via
  `server_settings` on every connection (ADR-0011: schema-per-service).
- **aio-pika** for RabbitMQ; **httpx** for the plan worker's call to ai-service.
- **FastAPI** with a lifespan that owns the engine, the broker connection, the
  outbox relay task, the two consumers, and the plan worker.

## 4. Data model

Ten tables in the `catalog` Postgres schema (mirrored exactly by
`models.py` and `alembic/versions/0001_initial.py`). Three enums:
`source_type` (`video`|`playlist`), `course_state`
(`pending_ingestion`|`ready`|`failed`|`unsupported_source`), and `plan_state`
(`pending`|`ready`|`failed`).

```
sources ──1:1── courses ──1:N── lessons ──N:1── videos
                   │            │
                   │            └── progress (PK owner_id, lesson_id)
                   │
                   └──1:N── plans ──1:N── days ──M:N── lessons (via day_lessons)

outbox           (transactional outbox; relay drains to RabbitMQ)
message_dedupe   (PK message_id; consumers gate on this)
```

Key constraints / decisions:

- `owner_id` is the Zitadel `sub` **string** with **no FK** to identity's
  `users` table — services do not cross-reference schemas (ADR-0011).
  Ownership of every row is enforced in queries, and non-owners always see
  `404` (never `403`) so existence is not leaked.
- `videos.id` is a client-supplied UUID PK with `server_default
  gen_random_uuid()`. Ingestion's `video_id` is stored **directly** as
  `catalog.videos.id` (no separate `ingestion_video_id` column) — one ID
  across services (a locked-in cross-service decision).
- `lessons.unique(course_id, position)` — positions are unique within a
  course, so the plan worker re-assigns positions sequentially across the
  whole course after computing boundaries.
- `progress` PK is `(owner_id, lesson_id)`; the upsert is the hot path and is
  idempotent across resubmits/scrubbing.
- `outbox` has a partial index `catalog_outbox_unpublished_idx` on
  `published_at IS NULL` — exactly what the relay's fetch query targets.
- Timestamps are `DateTime(timezone=True)`; numeric lesson ranges are
  `Numeric(10,3)` and cast to `float` at the boundary for JSON.

## 5. REST API

All routes are under `/v1`, exposed publicly by KrakenD as `/api/catalog/*`.
All except `/healthz` require `X-User-Id` (via `shared.auth.current_user`).

| Method | Path | Body / Query | Success | Errors |
|---|---|---|---|---|
| GET  | `/healthz` | — | 200 `{status,db,schema}` | — |
| POST | `/v1/courses` | `{source_url, title?}` | 201 `CourseResponse` | 409 quota / dup URL, 422 |
| GET  | `/v1/courses` | `?state&limit&cursor` | 200 `[CourseResponse]` | 422 bad state |
| GET  | `/v1/courses/{id}` | — | 200 `CourseDetailResponse` (with lessons + progress) | 404 |
| DELETE | `/v1/courses/{id}` | — | 204 | 404 |
| GET  | `/v1/courses/{id}/lessons` | — | 200 `[LessonResponse]` | 404 |
| PUT  | `/v1/lessons/{id}/progress` | `{watched_seconds}` | 200 `ProgressResponse` | 404, 422 |
| POST | `/v1/courses/{id}/plans` | `{mode, target_days?, starts_on?, manual_days?}` | 201 `PlanResponse` | 404, 422 |
| GET  | `/v1/plans/{id}` | — | 200 `PlanDetailResponse` (days→lessons + progress) | 404 |

### Progress math
`progress_percent(watched, start, end) = clamp(watched / (end-start) * 100, 0, 100)`.
Zero-duration lesson → 0%. `watched` past the end → 100% (percent); the raw
`watched_seconds` is still stored unchanged. The same helper in
`repository.progress_percent` is reused by `plan_service` so the figure is
identical everywhere.

### The create-course flow (transactional outbox)
`POST /courses` is the most important write:
1. `quota_allows_course_create` counts the owner's courses in the last 24h and
   rejects (409) **before any row is written** — the outbox and ingestion
   never hear about an over-limit request.
2. `create_source_and_course` inserts `Source` + `Course` (state
   `pending_ingestion`) + an `Outbox` row holding the serialized
   `IngestSourceCommand`, all in **one commit**. A crash can never orphan a
   Source without the command that tells ingestion to fetch it.
3. `outbox.id == command_id` so a row maps 1:1 to a command for traceability.
4. The outbox relay drains the row to RabbitMQ and stamps `published_at`. A
   mid-batch crash only re-sends rows whose stamp never landed; ingestion's
   dedupe absorbs the replay.

## 6. Messaging

Two directions, both on RabbitMQ, both using the shared contracts.

### Out: IngestSource command (outbox relay)
- `outbox_relay.fetch_unpublished` selects up to 100 oldest
  `published_at IS NULL` rows (hitting the partial index), the relay
  publishes each via `shared.rabbit.publish` to routing key
  `catalog.command.ingest_source`, then `mark_published` stamps the row.
- Runs as a background `asyncio.Task` with its own short-lived sessions,
  independent of HTTP traffic. The shared `shared.outbox.run_outbox_relay`
  loop is framework-agnostic; catalog only plugs in fetch/mark/publish.

### In: ingestion events (consumers)
- `catalog.events.videos_discovered` ← `ingestion.event.videos_discovered`
  - `_dedupe` inserts into `message_dedupe` keyed by `event_id`; a duplicate
    raises `UniqueViolationError` → the consumer acks **without work** (the
    replay-absorption that makes the outbox safe).
  - Upserts `Video` on `(source_id, youtube_video_id)` with
    **`id = v.video_id`** (propagated UUID).
  - Creates one **whole-video Lesson** per video (`start=0`,
    `end=duration_seconds`) until ai-service splits it into boundary lessons.
- `catalog.events.course_ready` ← `ingestion.event.course_ready`
  - Maps ingestion states to `course_state`: `ready→ready`,
    `unsupported_source→unsupported_source`, everything else → `failed`.
  - Plain `UPDATE`; a stale terminal event for a missing course is logged and
    acked so it never blocks the queue.
- Failed messages go to `learnpilot.dlq.catalog.videos_discovered` /
  `learnpilot.dlq.catalog.course_ready` (retry-via-DLX, declared in
  `shared/rabbit.py`).

`messaging.startup`/`shutdown` (wired in `main.py` lifespan) own the
connection, channel, topology declaration, the relay task, and the two
consumers. If `RABBITMQ_URL` is unset the REST API still works and outbox
rows accumulate — handy for tests.

## 7. Plans and the async worker

A **Plan** schedules a Course's Lessons across **Days**. Two modes:

- **`manual`** — synchronous. The User supplies `manual_days`: a list of
  lists of Lesson ids (one inner list per Day). `create_manual_plan`
  validates every lesson id belongs to the course (else 422), then creates
  `Plan` (state `ready`) + `Day`s + `DayLesson`s in **one transaction**.
- **`complete_in_days`** — asynchronous. `create_auto_plan` creates the
  `Plan` row with state `pending` and returns immediately. The plan worker
  picks it up.

### The plan worker (`plan_worker.run_plan_worker`)
- A background task in `main.py` lifespan, gated on `PLAN_WORKER_ENABLED`.
- Polls for `state=pending` AND `mode=complete_in_days` plans every 3s.
- `plan_service.generate_plan` is the per-plan unit of work and is
  **idempotent**: it deletes the plan's prior `Day`s/`Lesson`s first, so a
  crashed-and-restarted plan regenerates cleanly.
- For each of the course's Videos it calls
  `POST {AI_SERVICE_URL}/v1/topic-boundaries` via `httpx` (10s timeout),
  propagating `X-User-Id` (ADR-0010). On **any** HTTP/connection error it
  marks the plan `state=failed` and moves on — the Plan has no
  `failure_reason` column; the decision was to **fail gracefully and
  visibly**, not to store a diagnosis.
- On success it rebuilds `Lesson`s from the boundary ranges, assigning
  sequential `position`s across the whole course (so
  `unique(course_id, position)` holds), then distributes the lessons across
  `target_days` **by count** using `divmod` (first remainder days get one
  extra; empty days are valid when there are fewer lessons than days).
  **Boundaries come from the LLM — never from even time cuts** (the plan
  forbids even time cuts, which is why the worker exists at all).
- `planned_date = starts_on + (day_index-1)` when `starts_on` is set, else
  `None`. Sets `state=ready`.

### Plan reads
`GET /v1/plans/{id}` runs a single query joining `Plan→Course` (ownership
filter → 404 for non-owners), `Day`, `DayLesson`, `Lesson`, and outerjoin
`Progress` on `owner_id`, building the nested `days→lessons` tree with
`progress_percent` per lesson — one round trip, no N+1.

## 8. Configuration

`CatalogSettings` extends `shared.config.Settings`:

| Setting | Default | Meaning |
|---|---|---|
| `DB_SCHEMA` | `catalog` | schema name + search_path for every connection |
| `DATABASE_URL` | (shared) | Postgres DSN; `async_dsn` adds the `+asyncpg` driver |
| `RABBITMQ_URL` | (shared) `None` | broker; `None` disables messaging gracefully |
| `AI_SERVICE_URL` | `http://ai-service:8000` | plan worker's topic-boundaries target |
| `COURSE_CREATE_DAILY_LIMIT` | `50` | 24h rolling quota per owner |
| `PLAN_WORKER_ENABLED` | `True` | start the in-process plan worker in lifespan |

## 9. Running

The Dockerfile mirrors identity-service: `PYTHONPATH=/app`, copies
`shared/` alongside the service, runs `alembic upgrade head` then
`uvicorn main:app`. Locally in the Compose stack:

```
docker compose up catalog-service
# health: http://localhost:8000/healthz  (via KrakenD: /api/catalog/healthz)
# self-check (needs DATABASE_URL + the catalog schema migrated):
#   docker compose run --rm catalog-service uv run python -m smoke
```

`smoke.py` is an asyncpg self-check (no SQLAlchemy/FastAPI imports at module
top, so it runs even where those deps aren't installed on the host). It
exercises the plan DoD invariants: `progress_percent` caps, the
`message_dedupe` dedupe gate (duplicate insert raises `UniqueViolationError`),
the outbox fetch+mark cycle, and per-owner course/progress isolation. Cleanup
deletes only the exact smoke row ids (never a broad sweep that could wipe
real consumer rows).

## 10. Source map

```
services/catalog/
├── main.py              # FastAPI app, lifespan (engine + messaging + plan worker)
├── config.py            # CatalogSettings
├── db/session.py        # async engine (search_path=catalog), get_session dependency
├── models.py            # 10 ORM tables + 3 enums
├── schemas.py           # Pydantic request/response models (REST)
├── repository.py        # progress_percent + transactional outbox + reads + progress upsert
├── quota.py             # 24h rolling course-create pre-check
├── routes.py            # /v1 course/lesson/progress endpoints
├── outbox_relay.py      # drains catalog.outbox to RabbitMQ
├── consumers.py         # VideosDiscovered + CourseReady consumers
├── messaging.py         # startup/shutdown: connect, declare, start relay + consumers
├── plan_schemas.py      # Plan request/response models
├── plan_service.py      # manual/auto create, plan detail read, generate_plan (worker body)
├── plan_routes.py       # /v1 plan endpoints
├── plan_worker.py       # polling loop driving generate_plan
├── smoke.py             # asyncpg self-check
├── pyproject.toml       # deps
├── Dockerfile
├── alembic.ini
└── alembic/
    ├── env.py           # async-aware Alembic env
    └── versions/0001_initial.py   # every table/enums/constraints/indexes; clean downgrade
```
