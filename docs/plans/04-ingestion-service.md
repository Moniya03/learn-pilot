Owner: Vasu

# ingestion-service plan

Purpose: own the per-Course ingestion saga and stage workers that turn a Source into cached metadata, transcripts, and Weaviate chunks.

Dependencies: catalog-service publishes `IngestSource` and consumes ingestion events; ai-service consumes `VideoReady`; Phase 0 RabbitMQ/MinIO/Weaviate/shared. Public prefix: `/api/ingestion` for debug/admin only.

## DB schema: `ingestion`

```sql
create type ingestion.saga_state as enum (
  'pending','resolving','metadata_fetching','transcript_fetching','whispering','embedding',
  'ready','unsupported_source','transcript_unavailable','source_fetch_failed','failed'
);
create type ingestion.video_state as enum (
  'pending','metadata_ready','caption_ready','needs_whisper','transcript_ready','embedding','ready','failed','unsupported_source'
);

create table ingestion.sagas (
  id uuid primary key,
  owner_id text not null,                 -- User reference, no FK
  source_id uuid not null,                -- catalog Source reference, no FK
  course_id uuid not null,                -- catalog Course reference, no FK
  source_url text not null,
  source_type text not null check (source_type in ('video','playlist','unknown')),
  state ingestion.saga_state not null default 'pending',
  total_videos int not null default 0 check (total_videos >= 0),
  ready_videos int not null default 0 check (ready_videos >= 0),
  failure_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (course_id)
);
create index sagas_owner_idx on ingestion.sagas (owner_id, created_at desc);

create table ingestion.videos (
  id uuid primary key,
  saga_id uuid not null references ingestion.sagas(id) on delete cascade,
  owner_id text not null,
  course_id uuid not null,
  source_id uuid not null,
  youtube_video_id text not null,
  canonical_url text not null,
  title text,
  duration_seconds int check (duration_seconds is null or duration_seconds > 0),
  thumbnail_url text,
  position int not null,
  state ingestion.video_state not null default 'pending',
  captions_artifact_uri text,
  captions_checksum text,
  transcript_artifact_uri text,
  transcript_checksum text,
  audio_artifact_uri text,
  audio_checksum text,
  transcript_source text check (transcript_source is null or transcript_source in ('captions','whisper')),
  chunk_count int not null default 0 check (chunk_count >= 0),
  failure_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (saga_id, youtube_video_id),
  unique (course_id, youtube_video_id)
);
create index ingestion_videos_saga_idx on ingestion.videos (saga_id, position);
create index ingestion_videos_course_idx on ingestion.videos (course_id);

create table ingestion.stage_jobs (
  id uuid primary key,
  saga_id uuid not null references ingestion.sagas(id) on delete cascade,
  video_id uuid references ingestion.videos(id) on delete cascade,
  stage text not null check (stage in ('resolver','metadata','transcript','whisper','embed')),
  state text not null check (state in ('queued','running','succeeded','failed','dead_lettered')),
  attempts int not null default 0,
  last_error text,
  available_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index stage_jobs_lookup_idx on ingestion.stage_jobs (stage, state, available_at);

create table ingestion.quota_usage (
  owner_id text not null,
  quota_date date not null,
  whisper_seconds_used int not null default 0,
  embed_chunks_used int not null default 0,
  primary key (owner_id, quota_date)
);

create table ingestion.outbox (
  id uuid primary key,
  routing_key text not null,
  message jsonb not null,
  published_at timestamptz,
  created_at timestamptz not null default now()
);
create index ingestion_outbox_unpublished_idx on ingestion.outbox (created_at) where published_at is null;

create table ingestion.message_dedupe (
  message_id uuid primary key,
  message_type text not null,
  processed_at timestamptz not null default now()
);
```

Postgres stores artifact URI + checksum, not raw audio/caption/transcript bytes.

## REST endpoints

All require `X-User-Id`; intended for debug/admin. KrakenD should require admin scope for retry endpoints.

### `GET /v1/sagas/{saga_id}`

Purpose: inspect saga state.

```python
class IngestionVideoResponse(BaseModel):
    id: UUID
    youtube_video_id: str
    title: str | None
    position: int
    state: str
    chunk_count: int
    failure_reason: str | None

class SagaResponse(BaseModel):
    id: UUID
    course_id: UUID
    source_id: UUID
    state: str
    total_videos: int
    ready_videos: int
    failure_reason: str | None
    videos: list[IngestionVideoResponse]
```

Auth: owner or admin scope.

### `POST /v1/sagas/{saga_id}/retry`

Purpose: requeue failed transient stage jobs.

```python
class RetrySagaRequest(BaseModel):
    stage: Literal['resolver','metadata','transcript','whisper','embed'] | None = None
    video_id: UUID | None = None
```

Response: `SagaResponse`.

Auth: admin scope.

### `GET /healthz`

No auth; checks DB, RabbitMQ, MinIO, Weaviate clients shallowly.

## RabbitMQ

Exchange: `learnpilot.topic`.

### Consumed command: `catalog.command.ingest_source`

Owned by catalog; consumed idempotently.

```python
class IngestSourcePayload(BaseModel):
    owner_id: str
    source_id: UUID
    source_url: AnyUrl
    source_type_hint: Literal['video','playlist','unknown'] = 'unknown'

class IngestSourceCommand(BaseCommand):
    command_id: UUID
    command_type: Literal['IngestSource']
    saga_id: UUID
    course_id: UUID
    video_id: None = None
    occurred_at: datetime
    payload: IngestSourcePayload
    schema_version: Literal[1] = 1
```

Queue: `catalog.commands.ingest_source`, routing key `catalog.command.ingest_source`.

### Internal stage commands

Each worker consumes one queue and schedules the next stage by publishing an internal command.

```python
class StageCommandPayload(BaseModel):
    owner_id: str
    source_id: UUID
    stage: Literal['resolver','metadata','transcript','whisper','embed']
    force: bool = False

class StageCommand(BaseCommand):
    command_type: Literal['RunStage']
    command_id: UUID
    saga_id: UUID
    course_id: UUID
    video_id: UUID | None
    occurred_at: datetime
    payload: StageCommandPayload
    schema_version: Literal[1] = 1
```

Queues/routing keys:

| queue | routing key | max attempts | terminal DLQ |
|---|---|---:|---|
| `ingestion.stage.resolver` | `ingestion.stage.resolver` | 3 | `learnpilot.dlq.ingestion.resolver` |
| `ingestion.stage.metadata` | `ingestion.stage.metadata` | 3 | `learnpilot.dlq.ingestion.metadata` |
| `ingestion.stage.transcript` | `ingestion.stage.transcript` | 3 | `learnpilot.dlq.ingestion.transcript` |
| `ingestion.stage.whisper` | `ingestion.stage.whisper` | 2 | `learnpilot.dlq.ingestion.whisper` |
| `ingestion.stage.embed` | `ingestion.stage.embed` | 3 | `learnpilot.dlq.ingestion.embed` |

Each has retry queue with TTL and DLX back to the stage routing key. Dedupe by `command_id` in `ingestion.message_dedupe`.

### Published events

All events are written to `ingestion.outbox` in the same DB transaction as state changes.

#### `VideosDiscovered`

Routing key: `ingestion.event.videos_discovered`; consumed by catalog.

```python
class DiscoveredVideo(BaseModel):
    video_id: UUID
    youtube_video_id: str
    canonical_url: AnyUrl
    title: str
    duration_seconds: int
    thumbnail_url: AnyUrl | None
    position: int

class VideosDiscoveredPayload(BaseModel):
    owner_id: str
    source_id: UUID
    videos: list[DiscoveredVideo]

class VideosDiscoveredEvent(BaseEvent):
    event_id: UUID
    event_type: Literal['VideosDiscovered']
    saga_id: UUID
    course_id: UUID
    video_id: None = None
    occurred_at: datetime
    payload: VideosDiscoveredPayload
    schema_version: Literal[1] = 1
```

#### `VideoReady`

Routing key: `ingestion.event.video_ready`; consumed by ai-service.

```python
class VideoReadyPayload(BaseModel):
    owner_id: str
    source_id: UUID
    youtube_video_id: str
    transcript_artifact_uri: str
    transcript_checksum: str
    transcript_source: Literal['captions','whisper']
    chunk_count: int

class VideoReadyEvent(BaseEvent):
    event_id: UUID
    event_type: Literal['VideoReady']
    saga_id: UUID
    course_id: UUID
    video_id: UUID
    occurred_at: datetime
    payload: VideoReadyPayload
    schema_version: Literal[1] = 1
```

#### `CourseReady`

Routing key: `ingestion.event.course_ready`; consumed by catalog.

```python
class CourseReadyPayload(BaseModel):
    owner_id: str
    source_id: UUID
    state: Literal['ready','unsupported_source','transcript_unavailable','source_fetch_failed','failed']
    failure_reason: str | None = None

class CourseReadyEvent(BaseEvent):
    event_id: UUID
    event_type: Literal['CourseReady']
    saga_id: UUID
    course_id: UUID
    video_id: None = None
    occurred_at: datetime
    payload: CourseReadyPayload
    schema_version: Literal[1] = 1
```

## Saga state machine

```text
pending
  -> resolving
  -> metadata_fetching
      -> unsupported_source          (age-restricted/members/region/live/premiere/refused-duration)
      -> source_fetch_failed         (yt-dlp transient exhausted)
  -> transcript_fetching
      -> whispering                  (no usable captions)
      -> transcript_unavailable      (captions missing and Whisper exhausted)
  -> embedding
  -> ready
  -> failed                          (unexpected terminal)
```

Per-Video fan-out/fan-in:

1. `IngestSource` creates saga, queues resolver.
2. Resolver runs `yt-dlp --flat-playlist` or single URL resolve; creates `ingestion.videos` rows.
3. Metadata workers fetch full metadata for each Video. When all metadata ready, publish `VideosDiscovered`.
4. Transcript worker tries captions first. If usable, writes `captions/` and `transcripts/` MinIO artifacts and queues embed. If no captions, queues whisper.
5. Whisper worker downloads audio to `audio/`, calls Groq Whisper, writes transcript artifact, deletes audio, queues embed.
6. Embed worker chunks transcript (~512 tokens / ~64 overlap), prefixes `passage: `, calls HF e5, deletes existing Weaviate chunks by `video_id`, inserts new chunks.
7. Each embedded Video publishes `VideoReady`. When `ready_videos == total_videos`, saga publishes `CourseReady`.

## External integrations

- yt-dlp:
  - resolve flat playlist/single Video;
  - metadata JSON;
  - captions/subtitles;
  - audio download for Whisper fallback;
  - pinned version + scheduled smoke test.
- MinIO buckets:
  - `audio`: ephemeral; delete after transcription and on failure cleanup;
  - `captions`: cached by `youtube_video_id`;
  - `transcripts`: cached by `youtube_video_id` + checksum.
- Groq Whisper: `whisper-large-v3` through shared client.
- HF embeddings: `intfloat/multilingual-e5-large`, 1024 dimensions, via shared client.
- Weaviate: one `TranscriptChunk` collection, props `text`, `course_id`, `video_id`, `chunk_index`, `start`, `end`; delete-by-video-id before insert.
- Quotas: worker checks `ingestion.quota_usage` before Whisper/audio and embed dispatch; increments in same transaction as successful stage state.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| ING-1 | Service skeleton + migrations | INF-8 | M | schema applies; health checks DB/Rabbit/MinIO/Weaviate. |
| ING-2 | Rabbit consumers + outbox relay | ING-1 | L | consume command, publish outbox events, dedupe by message id, retry/DLQ wired. |
| ING-3 | Saga creation from `IngestSource` | ING-2, CAT-3 | M | idempotent command creates one saga per Course and queues resolver. Coordination required with Moniya. |
| ING-4 | Resolver worker | ING-3 | L | single Video/playlist resolve, unsupported_source terminal mapping, Video rows created. |
| ING-5 | Metadata worker + `VideosDiscovered` | ING-4 | M | full metadata cached and catalog event emitted once. Coordination required with Moniya. |
| ING-6 | Transcript worker | ING-5 | L | captions fetched/cached; fallback to whisper queued when needed. |
| ING-7 | Whisper worker | ING-6 | L | audio downloaded, Groq transcription saved, audio cleaned up, quota enforced. |
| ING-8 | Embed worker | ING-6, ING-7 | L | chunking with timestamps, E5 embeddings, Weaviate delete/insert, embed quota enforced. |
| ING-9 | Fan-in events | ING-8 | M | emits per-Video `VideoReady` and final `CourseReady` atomically. Coordination required with Moniya and Vasu(ai). |
| ING-10 | Retry/admin endpoints | ING-2 | S | saga inspect/retry works under admin scope. |
| ING-11 | Tests/smoke | ING-4..9 | M | fake yt-dlp + fake clients exercise happy path, unsupported_source, DLQ, idempotency. |

## Cross-service dependencies

- Consumes catalog-owned `IngestSource` — coordination required with Moniya.
- Publishes `VideosDiscovered` and `CourseReady` to catalog — coordination required with Moniya.
- Publishes `VideoReady` to ai-service — Vasu owns both but keep the shared schema stable.

## Open questions / assumptions

- Exact unsupported duration threshold is product config.
- Transcript artifact JSON shape follows normalized timestamped items: `{text,start,end}`.
- Course delete cleanup event is not specified; if catalog adds it, ingestion should delete MinIO artifacts and Weaviate chunks by `course_id`/`video_id`.
