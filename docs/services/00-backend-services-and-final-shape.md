# LearnPilot backend — services, why each exists, and the final shape

> This document explains **what each backend service does, why it exists as a
> separate process, how the services talk to each other**, and **what the whole
> backend looks like once every plan is implemented**. It is the companion to
> the per-service deep-dives in this folder (`01-catalog-service.md`, …).
> Plans live in `docs/plans/`; architectural decisions in `docs/adr/`;
> domain language in `CONTEXT.md`.

## 1. The one-sentence version

LearnPilot turns a YouTube URL into a **structured, schedulable, trackable
course** with an AI-assisted markdown notebook — and it does that across five
small FastAPI services behind a KrakenD auth gateway, sharing a Postgres
(schema-per-service), a RabbitMQ broker, a MinIO object store, and a Weaviate
vector index.

The User-facing flow is:

```
paste a URL  ─► catalog creates Source+Course, asks ingestion to fetch
                 ingestion resolves the playlist, fetches metadata+transcript
                   (Whisper when captions are missing), embeds chunks into Weaviate
                 ingestion tells catalog "videos discovered" + "course ready"
               catalog materializes Videos + whole-video Lessons
User picks "complete in N days"  ─► catalog plan worker asks ai for topic boundaries
                 ai returns natural LLM boundaries (never even time cuts)
               catalog rebuilds Lessons from boundaries, schedules Days
User watches  ─► player reports watched position  ─► catalog stores Progress (0–100%)
User writes notes  ─► notes stores markdown anchored to Lesson/time
User asks a question or wants a summary  ─► ai retrieves from Weaviate, answers/summarizes
```

## 2. The five services and their one-line jobs

| Service | Public prefix | Owns | One-line job |
|---|---|---|---|
| **identity-service** | `/api/identity` | `identity.users` | Local profile for the Zitadel `sub`; find-or-create on login. |
| **catalog-service** | `/api/catalog` | `catalog.*` (10 tables) | Content & learning state: Source/Course/Video/Lesson/Plan/Day/Progress. |
| **ingestion-service** | `/api/ingestion` (admin only) | `ingestion.*` + artifacts | The heavy pipeline: resolve → metadata → transcript → Whisper → embed. |
| **ai-service** | `/api/ai` | `ai.*` + Weaviate retrieval | AI surface: Q&A, Summary cache, topic boundaries for plan splitting. |
| **notes-service** | `/api/notes` | `notes.*` | Notebook and Note markdown CRUD anchored to a Course/Lesson. |

Three shared dependencies every service reads (never redefines):

- **`shared/auth.py`** — `current_user` FastAPI dependency reading `X-User-Id`
  from KrakenD (ADR-0010). No service ever sees a JWT.
- **`shared/config.py`** — common pydantic-settings base (`DATABASE_URL`,
  `RABBITMQ_URL`, …); each service subclasses it.
- **`shared/events.py` / `shared/rabbit.py` / `shared/outbox.py`** — the
  RabbitMQ envelopes, topology/declare helpers, and the framework-agnostic
  outbox relay loop. Built with catalog; consumed by ingestion and ai.

## 3. Why this particular split

**Different rates of change, different scaling profiles.**
- catalog and notes are mostly relational CRUD with low, predictable CPU —
  they should not be scaled (or billed) alongside a GPU Whisper job.
- ingestion is a long, stateful, retry-heavy pipeline with artifacts and per-
  video stage jobs; it wants its own queue topology, its own workers, and its
  own failure modes. Forcing it into the catalog CRUD process would block the
  event loop and conflate the two failure surfaces.
- ai is LLM-shaped (latency, token quota, prompt caching, vector retrieval)
  and benefits from being scaled/tuned independently of the data it reads.

**The trust boundary stays sharp.** Only KrakenD validates the JWT and emits
`X-User-Id`; every service trusts only that header. Splitting services would
not help if they could be bypassed, so the gateway is the single choke point
and services bind to the private Docker network only.

**Schema-per-service (ADR-0011) without a service mesh.** Each service owns a
Postgres schema; cross-service references are **logical IDs** (`owner_id`,
`course_id`, `video_id`), never cross-schema FKs. Coordination happens via
RabbitMQ events and a few HTTP calls, not shared tables. This keeps deploys
independent and avoids a distributed-monolith where every join reaches across
schemas.

**Hybrid inter-service comms (ADR-0012).** State changes that must be durable
and replayable travel as RabbitMQ events/commands through a transactional
outbox. Read-time lookups that need a fresh answer (catalog validating a
Lesson id for notes, the plan worker asking ai for boundaries) are plain HTTP
calls inside the private network. The choice is per-need, not dogmatic.

## 4. Service-by-service deep dive

### 4.1 identity-service — *who are you?*

**Why it exists:** The rest of the backend needs a stable, local notion of a
User without depending on Zitadel at request time. identity-service is the
canonical home of the `User` table keyed by the Zitadel `sub`.

**What it does:** On `GET /v1/me` it find-or-creates the `User` from the
injected `X-User-Id` (sub) and `X-User-Email` headers. It also owns `PATCH
/v1/me` (display name, avatar) and `GET /v1/users/{owner_id}` for other services
to resolve display info. It stores **no** tokens — Zitadel, Google, access, or
refresh (ADR for Google-only login is ADR-0006). Last login is stamped.

**Why it is separate:** It is tiny and stable; pulling it into catalog would
create an awkward coupling where the content service also owns identity. Other
services reference Users only by the opaque `owner_id` string and resolve
display info through this service when they need to.

**Status:** Fully implemented (ID-1…ID-6), raw asyncpg, per-service
`migrate.py`, emails validated at the trust boundary.

### 4.2 catalog-service — *the content and learning-state backbone*

**Why it exists:** Someone has to own the structural model — Source → Course
→ Video → Lesson → Plan → Day → Progress — and be the place the frontend
reads course state from. That data is relational, mostly-read, and changes
shape frequently with product needs. It belongs in a fast CRUD service
separated from the heavy pipeline that produces it.

**What it does:**
- **Create:** `POST /v1/courses` writes `Source + Course + Outbox` (an
  `IngestSourceCommand`) in one transaction → a relay drains the outbox to
  RabbitMQ, telling ingestion to fetch the URL. A 24h rolling quota pre-check
  rejects over-limit users **before any row is written**.
- **Consume:** `VideosDiscovered` upserts Videos (with ingestion's `video_id`
  stored verbatim as `catalog.videos.id`) and creates whole-video Lessons;
  `CourseReady` maps ingestion states to `course_state`.
- **Read:** List/get/delete courses, list lessons, per-User Progress — each
  computed `progress_percent` (0–100) in a single joined query so the player UI
  never recomputes.
- **Plan:** `POST /v1/courses/{id}/plans` either builds the plan manually
  (state `ready`) or marks it `pending` for the async worker. The worker calls
  ai-service `/v1/topic-boundaries` per video to get **natural LLM boundaries
  (never even time cuts)**, rebuilds Lessons, and distributes them across
  `target_days` by count. Any ai error → plan `state=failed` (graceful).
- **Quota:** 24h rolling course-create limit, enforced before the outbox write.

**Why SQLAlchemy 2.x async + Alembic here (and raw asyncpg in identity):**
catalog's 9-table schema needs joins and eager `progress_percent` math; the ORM
pays for itself. identity is a single table — raw asyncpg is leaner there.

**Status:** Fully implemented (CAT-1…CAT-8), reviewed, smoke-tested. See
`01-catalog-service.md` for the full write-up.

### 4.3 ingestion-service — *the heavy pipeline*

**Why it exists:** Turning a YouTube URL into indexed, searchable transcript
chunks is slow, stateful, retry-heavy, GPU-bound work that should not share a
process with request-response CRUD. It owns its own saga + stage-job tables,
artifact storage, retry/DLQ topology, and per-owner Whisper/embedding quotas.

**What it does (per the plan):**
- Consumes `catalog.command.ingest_source` and creates a **saga** for the
  Course plus a row per discovered Video.
- **Stage pipeline**, driven by `ingestion.stage_jobs`:
  `resolver → metadata → transcript → whisper → embed`.
  Captions are preferred; Whisper fills in when captions are absent/missing
  (ADR-0007: yt-dlp is used defensively and its output is cached).
  Artifacts (audio, captions, transcripts) land in **MinIO** (ADR-0005);
  Postgres stores only URIs + checksums.
  Transcript chunks are embedded with HF **multilingual-e5-large** (ADR-0003)
  and indexed into **Weaviate**'s `TranscriptChunk` collection (ADR-0002).
- Publishes `VideosDiscovered` (incremental, as videos resolve) and, when a
  video is fully ready, `VideoReady` (consumed by ai-service to build its
  video index). When the whole Course is ready/failed, publishes `CourseReady`
  (consumed by catalog).
- All async, with per-stage retry via `available_at` and a dead-letter path.
- Debug/admin REST: `GET /v1/sagas/{id}` to inspect, `POST /v1/sagas/{id}/retry`
  to requeue; intended to be admin-scope-gated at KrakenD.

**Why it is separate:** GPU/Whisper work, a stage-job scheduler, artifact
check-summing, and a 5-stage state machine are a different kind of system than
relational CRUD. Co-locating them would couple catalog's failure modes to
network/Whisper/Weaviate health. Owner: Vasu, so it also maps cleanly onto
team ownership boundaries.

**Status:** Plan only (not yet implemented).

### 4.4 ai-service — *the AI surface*

**Why it exists:** LLM work has its own latency profile, token quotas, and
caching needs, and benefits from being tuned/logged independently. It also
owns the Weaviate retrieval/Q&A path, which is conceptually separate from
ingestion's write-side embedding.

**What it does (per the plan):**
- Consumes `VideoReady` from ingestion to build `ai.video_indexes` — a local
  pointer (video_id, transcript URI + checksum, chunk_count) so ai never
  reaches into ingestion's schema.
- **`POST /v1/qna`** — answers a User question scoped to one Video (or Course-
  wide on opt-in): embed the question with HF e5, retrieve top-k chunks from
  Weaviate, run LiteLLM/Groq (ADR-0004: hosted inference at start), return
  answer markdown + chunk citations. Token-quota-checked before the LLM call.
- **`POST /v1/summaries`** — create-or-return-cached Summary for a Video or
  Lesson scope; cache keyed on `(owner, video, scope, lesson, transcript
  checksum, prompt_hash)` so a transcript change invalidates cleanly.
- **`POST /v1/topic-boundaries`** — the endpoint the catalog plan worker
  calls. Returns natural LLM topic boundaries with titles/reasons; **never
  even time cuts** (this is the entire reason catalog's plan worker exists).
  Cached per `(owner, video, transcript_checksum, prompt_hash)`.
- Per-owner token quota in `ai.quota_usage`.

**Why it is separate:** Token quotas, prompt caches, model selection, and
vector retrieval each want their own observability and scaling. Owner: Vasu.

**Status:** Plan only (not yet implemented).

### 4.5 notes-service — *the markdown notebook*

**Why it exists:** Notes are discrete markdown blocks tied to a Course and
optionally anchored to a Lesson/time — a different shape and access pattern
than course/progress, and they have their own CRUD and (eventually) search
needs. Keeping them out of catalog keeps catalog's reads cheap and its
surface small.

**What it does (per the plan):**
- One **Notebook** per (User, Course); a Course that's deleted cascades
  manually (no cross-schema FK).
- **Notes** are markdown blocks; each may anchor to a `lesson_id` and a
  `video_timestamp`. `course_id` is copied onto the note for fast owner/course
  filtering without a join.
- Endpoints: `GET /v1/courses/{id}/notebook` (get-or-create),
  `GET /v1/courses/{id}/notes` (filter by lesson, paginated),
  `POST /v1/courses/{id}/notes`, `PATCH /v1/notes/{id}`, `DELETE /v1/notes/{id}`.
- **Cross-service:** on Note create/update, notes calls **catalog-service via
  HTTP** to validate Course ownership (and Lesson-in-Course when anchored),
  propagating `X-User-Id` through KrakenD. No RabbitMQ in v1.

**Why it is separate:** Markdown storage and (later) full-text search are a
different workload than the structured course model; coupling them would bloat
catalog's reads and force a schema migration into catalog for every notes
feature. Owner: Moniya.

**Status:** Plan only (not yet implemented). Depends on catalog's Course/Lesson
ownership endpoints (CAT-2 / CAT-REST) — already delivered.

## 5. The shared contracts (filling the Phase 0 gap)

Phase 0 INF-8 delivered `shared/auth.py` and `shared/config.py` but left the
messaging contracts open. The catalog work built the rest:

- **`shared/events.py`** — `Envelope` + `BaseEvent`/`BaseCommand` with a
  `Literal` discriminator and `event_id`/`occurred_at`. Typed contracts for
  `IngestSourceCommand` (routing key `catalog.command.ingest_source`),
  `VideosDiscoveredEvent`, `VideoReadyEvent`, `CourseReadyEvent`, plus a
  `ROUTING_KEY_BY_EVENT` map. Ingestion's `video_id` is carried verbatim and
  catalog stores it directly as `catalog.videos.id` — one ID across services.
- **`shared/rabbit.py`** — `connect`, `declare_topology` (exchanges + retry-
  via-DLX queues), `declare_queue`, `publish` with mandatory routing. Failed
  messages land in `learnpilot.dlq.<service>.<event>` for inspection.
- **`shared/outbox.py`** — `run_outbox_relay` loop: fetch unpublished → publish
  → mark published. Catalog plugs in fetch/mark/publish; ingestion and ai
  reuse the same loop with their own table mappers.

## 6. How the services talk to each other

### 6.1 Durable async (RabbitMQ via transactional outbox)

```
catalog ──IngestSource command──► ingestion       (catalog writes outbox row + Source+Course in one txn)
ingestion ──VideosDiscovered event──► catalog    (incremental video upserts)
ingestion ──VideoReady event──► ai               (ai builds its video index)
ingestion ──CourseReady event──► catalog          (terminal course state)
```

Each publisher writes to its **own** `outbox` table in the same transaction
as its state change, then a relay drains to the broker. Each consumer dedupes
by `event_id` against a `message_dedupe` table and acks-without-work on a
replay — so the outbox relay can crash and re-send safely.

### 6.2 Read-time HTTP (private network)

```
notes    ──GET catalog /v1/courses/{id}, /v1/courses/{id}/lessons (ownership validation)
catalog  ──POST ai     /v1/topic-boundaries (plan worker, per video)
```

These need a fresh answer at call time — RabbitMQ would be the wrong tool. They
propagate `X-User-Id` and stay on the private Docker network; KrakenD is not
in the path (it is the public edge only).

### 6.3 What is NOT shared
- No cross-schema FKs. `owner_id`, `course_id`, `video_id`, `lesson_id`,
  `source_id` are logical references; ownership is re-validated by the owning
  service when a caller needs to trust it.
- No shared DB transaction. The transactional outbox + dedupe is how eventual
  consistency is achieved without a distributed transaction.
- No tokens beyond KrakenD. services trust the header only.

## 7. The full request lifecycle once everything is built

 End-to-end, from "paste a URL" to "ask a question":

1. **User signs in** via Google through Zitadel → KrakenD validates the JWT,
   strips any forged inbound trusted headers, injects `X-User-Id`/`X-User-Email`,
   and routes to a backend service.
2. **Paste a URL.** `POST /api/catalog/courses` → catalog's quota pre-check
   passes → `Source + Course(state=pending_ingestion) + Outbox(IngestSource)`
   committed → 201 returned immediately. The outbox relay publishes
   `catalog.command.ingest_source`.
3. **ingestion receives the command**, creates a saga, runs
   `resolver → metadata → transcript → whisper → embed` per video. Captions
   preferred; Whisper when missing. Artifacts to MinIO; chunks to Weaviate.
   Publishes `VideosDiscovered` as videos resolve, `VideoReady` as each
   finishes, and `CourseReady` when the course is done.
4. **catalog consumes `VideosDiscovered`**, idempotently upserts Videos
   (with the propagated `video_id`) and creates whole-video Lessons.
5. **ai consumes `VideoReady`**, builds `ai.video_indexes` (storing the
   transcript URI + checksum + chunk count, not the bytes).
6. **catalog consumes `CourseReady`**, sets `course_state` to `ready`
   (or `failed`/`unsupported_source`).
7. **User opens the course.** `GET /api/catalog/courses/{id}` returns the
   Course with Lessons + per-User `progress_percent` in a single query. The
   player plays the Lesson's Video range.
8. **progress reporting.** `PUT /api/catalog/lessons/{id}/progress` with the
   watched position → idempotent upsert into `progress` → 0–100% derived.
9. **User picks "complete in N days".** `POST /api/catalog/courses/{id}/plans`
   with `mode=complete_in_days, target_days=N` → plan created in `pending`.
10. **catalog's plan worker** polls, and for the pending plan calls
    `POST /api/ai/topic-boundaries` per video (LLM boundaries — never even
    time cuts), rebuilds Lessons from boundaries, distributes them across N
    Days by count, and marks the Plan `ready`. On any ai error → `failed`,
    surfaced to the User; they can retry from the UI.
11. **User asks a question.** `POST /api/ai/qna` → ai embeds the question,
    retrieves chunks from Weaviate scoped to the Video (or Course), runs the
    LLM, returns markdown + citations, and decrements the token quota.
12. **User wants a summary.** `POST /api/ai/summaries` → cached by transcript
    checksum + prompt hash; a fresh transcript invalidates cleanly.
13. **User takes notes.** `POST /api/notes/courses/{id}/notes` with markdown,
    optional `lesson_id` + `video_timestamp` → notes validates ownership via
    catalog HTTP, stores the markdown, anchors it to the Lesson/time.
14. **User deletes the course.** `DELETE /api/catalog/courses/{id}` → catalog
    cascades its own rows; notes cleanup is a later product decision (no
    cross-schema cascade).

## 8. The final deployable shape

```
                       public                        private (Docker network `learnpilot_private`)
                    ┌────────────┐
   User ──HTTPS──►  │  web (Next) │── OIDC ─► Zitadel ──┐ (shares `zitadel` Postgres DB)
                    └─────┬──────┘                     │
                          │ /api/*                     ▼
                    ┌────────────┐                validates JWT,
                    │  KrakenD   │◄── JWKS ────── Zitadel
                    │  gateway   │  strips forged trusted headers,
                    └─────┬──────┘  injects X-User-Id/X-User-Email
          ┌──────────────┼──────────────────────────────────────┐
          ▼              ▼              ▼              ▼         ▼
   identity-service  catalog-service  notes-service  ingestion   ai-service
   (FastAPI)         (FastAPI)        (FastAPI)       (FastAPI)   (FastAPI)
        │                │                │              │           │
        │            outbox │           calls            outbox      consumes
        │              │   │ catalog via HTTP              │        VideoReady
        │              ▼   │                               ▼            │
        │          ┌─────────────┐                  ┌─────────────┐      │
        │          │  RabbitMQ    │◄──  events  ───►│ (relay+DLQ) │      │
        │          │  (3-manage)  │   commands      └─────────────┘     │
        │          └─────────────┘                                     ▼
        │                                                              │
        │              │                                                  │
        ▼              ▼                                                  ▼
  identity schema  catalog schema                                Weaviate (chunks)
        ↑          lessons/courses/                               MinIO (artifacts)
        └──────────┘   plans/etc.                                      │
                                                                       ▼
                                                                  LiteLLM/Groq
                                                                       │
                                  ┌────────────────────────────────────┘
                                  ▼
                          Postgres (per-service schema)
                          identity | catalog | notes | ingestion | ai | zitadel
```

**Process model:** six FastAPI processes (one per service) + KrakenD + Zitadel
+ Postgres + RabbitMQ + MinIO + Weaviate, all on one private network. Only
KrakenD (`:8080`) and web (`:3000`) publish public ports; every FastAPI app
binds privately. Each service migrates its own schema on startup (`alembic
upgrade head` for catalog; per-service `migrate.py` for identity) and reads
its connection details from shared env.

**Trust model:** only KrakenD holds/validates the JWT; services trust only
`X-User-Id`. No service-to-service auth in v1 (they're on the private network);
add mTLS/service tokens later if the trust boundary ever widens.

**Data model:** six Postgres schemas, zero cross-schema FKs. Catalog is the
structural backbone; ingestion and ai keep their own lightweight pointers
(`video_id`, `course_id`, transcript URIs + checksums) so they never reach
into catalog's schema. Notes keeps `course_id`/`lesson_id` as logical refs.

**Failure model:**
- **REST failures** → standard HTTP status; ownership hidden behind 404.
- **Async failures** → outbox + dedupe absorb replays; each stage job in
  ingestion has `available_at` retry and a dead-letter path; consumers use
  retry-via-DLX queues so a poisoned message parks in `learnpilot.dlq.*`.
- **AI failures** → the catalog plan worker marks the plan `failed` (graceful,
  visible); ai's own quota/cache owns its LLM reliability.
- **Broker down** → catalog's REST still works (outbox rows accumulate; the
  relay picks up on reconnect). `RABBITMQ_URL=None` is a supported config for
  tests.

## 9. Build order and current status

Per `docs/plans/README.md`, the suggested build order and where we are:

1. ✅ **Phase 0 infra** — repo layout, Compose, Postgres/RabbitMQ/Weaviate/
   MinIO/Zitadel/KrakenD, `shared/` (auth, config). Messaging contracts added
   during the catalog work.
2. ✅ **identity-service** — fully implemented.
3. ✅ **catalog-service** — fully implemented, reviewed, smoke-tested.
4. ⬜ **ingestion-service** — plan ready; depends on catalog's `IngestSource`
   command and the shared events (both now in place).
5. ⬜ **ai-service** — plan ready; depends on ingestion's `VideoReady` and on
   catalog calling `/v1/topic-boundaries` (the caller is built and degrades
   gracefully until ai exists).
6. ⬜ **notes-service** — plan ready; depends on catalog's Course/Lesson
   ownership endpoints (already delivered).
7. ⬜ **Hardening** — quotas beyond catalog's course-create limit, CI smoke
   across all `/healthz`, DLQ dashboards, shared lock files.

The next implementer of ingestion/ai/notes can run against the catalog and
shared contracts that already exist; no coordination work is pending on the
Moniya side beyond cross-service event payload agreements already encoded in
`shared/events.py`.

## 10. Cross-cutting decisions to keep in mind

- **IDs** are UUIDs except `owner_id`, which is the Zitadel `sub` string.
- **Public routes** are `/api/<service>/*`; **internal service paths** stay
  `/v1/*` (KrakenD rewrites). The plan worker and notes-to-catalog calls hit
  `/v1/*` on the private network directly.
- **Python stack** is FastAPI + Pydantic v2 + asyncpg, with SQLAlchemy 2.x +
  Alembic where joins justify it (catalog), raw asyncpg elsewhere (identity).
- **Quotas** are config values enforced at the service that owns the scarce
  resource: catalog (course create), ingestion (Whisper seconds + embed
  chunks), ai (LLM tokens).
- **Avoided terms** (`CONTEXT.md`): student/learner/segment/chunk (except
  `TranscriptChunk` in Weaviate, which is a different context)/schedule/
  roadmap/curriculum/completion/etc. Code and docs use the canonical terms:
  User, Source, Video, Lesson, Course, Plan, Day, Progress, Note, Notebook,
  Summary, Q&A.

When a later service needs to change a shared contract (an event payload, a
new trusted header, a new routing key), the producing service owns the change
and updates `shared/`; the consuming service reads it. The contract files in
`shared/` are the single source of truth — re-implementing them locally is the
one boundary this architecture explicitly forbids.