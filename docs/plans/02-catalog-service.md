Owner: Moniya

# catalog-service plan

Purpose: own Source, Video metadata, Course, Lesson, Plan, Day, and Progress; publish ingestion commands and consume ingestion events.

Dependencies: identity-service for User display lookup, ingestion-service events, ai-service topic-boundary HTTP. Public prefix: `/api/catalog`.

## DB schema: `catalog`

```sql
create type catalog.source_type as enum ('video', 'playlist');
create type catalog.course_state as enum ('pending_ingestion','ready','failed','unsupported_source');
create type catalog.plan_state as enum ('pending','ready','failed');

create table catalog.sources (
  id uuid primary key,
  owner_id text not null,                   -- User reference, no FK
  source_type catalog.source_type not null,
  original_url text not null,
  canonical_url text,
  title text,
  created_at timestamptz not null default now(),
  unique (owner_id, original_url)
);
create index sources_owner_idx on catalog.sources (owner_id, created_at desc);

create table catalog.courses (
  id uuid primary key,
  owner_id text not null,
  source_id uuid not null references catalog.sources(id) on delete cascade,
  title text not null,
  description text,
  thumbnail_url text,
  state catalog.course_state not null default 'pending_ingestion',
  failure_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id)
);
create index courses_owner_idx on catalog.courses (owner_id, created_at desc);

create table catalog.videos (
  id uuid primary key,
  owner_id text not null,
  source_id uuid not null references catalog.sources(id) on delete cascade,
  youtube_video_id text not null,
  title text not null,
  duration_seconds int not null check (duration_seconds > 0),
  thumbnail_url text,
  position int not null,
  transcript_available boolean not null default false,
  created_at timestamptz not null default now(),
  unique (source_id, youtube_video_id)
);
create index videos_source_order_idx on catalog.videos (source_id, position);

create table catalog.lessons (
  id uuid primary key,
  owner_id text not null,
  course_id uuid not null references catalog.courses(id) on delete cascade,
  video_id uuid not null references catalog.videos(id) on delete cascade,
  title text not null,
  position int not null,
  start_seconds numeric(10,3) not null default 0 check (start_seconds >= 0),
  end_seconds numeric(10,3) not null check (end_seconds > start_seconds),
  created_at timestamptz not null default now(),
  unique (course_id, position)
);
create index lessons_course_order_idx on catalog.lessons (course_id, position);
create index lessons_video_idx on catalog.lessons (video_id);

create table catalog.plans (
  id uuid primary key,
  owner_id text not null,
  course_id uuid not null references catalog.courses(id) on delete cascade,
  state catalog.plan_state not null default 'pending',
  mode text not null check (mode in ('complete_in_days','manual')),
  target_days int check (target_days is null or target_days > 0),
  starts_on date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index plans_course_idx on catalog.plans (course_id);

create table catalog.days (
  id uuid primary key,
  owner_id text not null,
  plan_id uuid not null references catalog.plans(id) on delete cascade,
  day_index int not null check (day_index > 0),
  planned_date date,
  created_at timestamptz not null default now(),
  unique (plan_id, day_index)
);

create table catalog.day_lessons (
  day_id uuid not null references catalog.days(id) on delete cascade,
  lesson_id uuid not null references catalog.lessons(id) on delete cascade,
  position int not null,
  primary key (day_id, lesson_id),
  unique (day_id, position)
);

create table catalog.progress (
  owner_id text not null,
  lesson_id uuid not null references catalog.lessons(id) on delete cascade,
  watched_seconds numeric(10,3) not null default 0 check (watched_seconds >= 0),
  updated_at timestamptz not null default now(),
  primary key (owner_id, lesson_id)
);
create index progress_owner_updated_idx on catalog.progress (owner_id, updated_at desc);

create table catalog.outbox (
  id uuid primary key,
  routing_key text not null,
  message jsonb not null,
  published_at timestamptz,
  created_at timestamptz not null default now()
);
create index catalog_outbox_unpublished_idx on catalog.outbox (created_at) where published_at is null;

create table catalog.message_dedupe (
  message_id uuid primary key,
  message_type text not null,
  processed_at timestamptz not null default now()
);
```

Progress is always derived from `watched_seconds / (lesson.end_seconds - lesson.start_seconds)`, capped to 0..100. No stored rollup.

## REST endpoints

All require `X-User-Id` from KrakenD.

### `POST /v1/courses`

Purpose: create Source + pending Course, publish `IngestSource` command.

```python
class CreateCourseRequest(BaseModel):
    source_url: AnyUrl
    title: str | None = None

class CourseResponse(BaseModel):
    id: UUID
    source_id: UUID
    owner_id: str
    title: str
    state: Literal['pending_ingestion','ready','failed','unsupported_source']
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
```

Auth: current User. Cheap quota pre-check: reject obvious over-limit Source requests before outbox insert; ingestion/ai enforce actual heavy-op quotas in their schemas.

### `GET /v1/courses`

Purpose: list current User's Courses.

Query: `state: str | None`, `limit: int=50`, `cursor: str | None`.

Response: `list[CourseResponse]`.

### `GET /v1/courses/{course_id}`

Purpose: Course detail.

Response:

```python
class LessonResponse(BaseModel):
    id: UUID
    video_id: UUID
    title: str
    position: int
    start_seconds: float
    end_seconds: float
    progress_percent: float

class CourseDetailResponse(CourseResponse):
    lessons: list[LessonResponse]
```

### `DELETE /v1/courses/{course_id}`

Purpose: delete Course owned by current User. Also publish delete cleanup command/event if added; coordinate with Vasu.

Response: `204 No Content`.

### `GET /v1/courses/{course_id}/lessons`

Purpose: ordered Lessons for player UI.

Response: `list[LessonResponse]`.

### `PUT /v1/lessons/{lesson_id}/progress`

Purpose: high-write batched player Progress upsert, expected every ~5s by frontend.

```python
class UpsertProgressRequest(BaseModel):
    watched_seconds: float = Field(ge=0)

class ProgressResponse(BaseModel):
    lesson_id: UUID
    watched_seconds: float
    progress_percent: float
    updated_at: datetime
```

Auth: current User owns Lesson's Course. Implement single-row upsert.

### `POST /v1/courses/{course_id}/plans`

Purpose: create a Plan; generation runs async in catalog worker.

```python
class CreatePlanRequest(BaseModel):
    mode: Literal['complete_in_days','manual']
    target_days: int | None = Field(default=None, gt=0)
    starts_on: date | None = None
    manual_days: list[list[UUID]] | None = None  # Lesson ids per Day, manual only

class PlanResponse(BaseModel):
    id: UUID
    course_id: UUID
    state: Literal['pending','ready','failed']
    mode: str
    target_days: int | None
    starts_on: date | None
```

Auth: current User. For auto-split, worker calls ai-service topic-boundary endpoint; never split by even time cuts.

### `GET /v1/plans/{plan_id}`

Response:

```python
class DayResponse(BaseModel):
    id: UUID
    day_index: int
    planned_date: date | None
    lessons: list[LessonResponse]

class PlanDetailResponse(PlanResponse):
    days: list[DayResponse]
```

### `GET /healthz`

No auth.

## RabbitMQ

Exchange: `learnpilot.topic`.

Publishes via `catalog.outbox`:

```python
class IngestSourcePayload(BaseModel):
    owner_id: str
    source_id: UUID
    source_url: AnyUrl
    source_type_hint: Literal['video','playlist','unknown'] = 'unknown'

class IngestSourceCommand(BaseCommand):
    command_type: Literal['IngestSource']
    command_id: UUID
    saga_id: UUID
    course_id: UUID
    video_id: None = None
    routing_key: Literal['catalog.command.ingest_source']
    payload: IngestSourcePayload
```

Consumes ingestion-owned events; exact payloads are in `04-ingestion-service.md`:

- `ingestion.event.videos_discovered` -> create/update `videos` and whole-video initial `lessons`.
- `ingestion.event.course_ready` -> set Course `ready` or terminal failure state.

Dedupe: insert `event_id` into `catalog.message_dedupe` first; if conflict, ack without work.

DLX/TTL/DLQ: queues use Phase 0 defaults: main queue -> retry queue TTL -> main queue; max attempts then `learnpilot.dlq.catalog.*`.

## External integrations

- YouTube IFrame is frontend-only; catalog only stores Lesson ranges and Progress.
- ai-service: catalog plan worker calls `/api/ai/v1/topic-boundaries` with trusted headers propagated.
- identity-service lookup only for display; never join schema.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| CAT-1 | Service skeleton + migrations | INF-8 | M | schema applies and `/healthz` passes. |
| CAT-2 | Course create/list/detail | CAT-1 | M | Source+Course transaction writes `IngestSource` outbox command. |
| CAT-3 | Outbox relay | CAT-2 | M | unpublished commands reach RabbitMQ and mark `published_at`. |
| CAT-4 | Ingestion event consumers | CAT-3, ING contract | L | `VideosDiscovered` creates Videos/Lessons; `CourseReady` updates Course state idempotently. Coordination required with Vasu. |
| CAT-5 | Progress upsert | CAT-1 | M | `PUT progress` handles 5s writes and returns computed percent. |
| CAT-6 | Plan CRUD + worker | CAT-5, AI-5 | L | manual plans work; auto plans call ai topic boundaries and create Days. Coordination required with Vasu. |
| CAT-7 | Quota pre-checks | CAT-2 | S | obvious over-limit Course creation is rejected before command publish; actual usage enforced by ingestion/ai. |
| CAT-8 | Tests | CAT-2..7 | M | endpoint ownership, progress math, consumer dedupe, outbox relay covered. |

## Cross-service dependencies

- Publishes `IngestSource` to ingestion-service — coordination required with Vasu.
- Consumes ingestion-owned `VideosDiscovered` and `CourseReady` — coordination required with Vasu.
- Calls ai-service topic-boundary endpoint for auto-split Plan generation — coordination required with Vasu.

## Open questions / assumptions

- Initial Lessons are whole Videos unless ai-service topic boundaries are requested for a Plan or long-Video split.
- Course delete cleanup event is not specified in ADRs; add only if Vasu needs async Weaviate/MinIO cleanup.
- Exact daily quota numbers are product config, not hard-coded.
