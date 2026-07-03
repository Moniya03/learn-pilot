Owner: Vasu

# ai-service plan

Purpose: own AI-assisted learning endpoints: Summary, Q&A, and LLM topic-boundary generation for Lesson/Plan splitting.

Dependencies: ingestion-service `VideoReady`, Weaviate transcript chunks, LiteLLM/Groq, HF query embeddings, catalog-service calls topic boundaries. Public prefix: `/api/ai`.

## DB schema: `ai`

```sql
create table ai.video_indexes (
  video_id uuid primary key,               -- catalog/ingestion Video reference, no FK
  owner_id text not null,                  -- User reference, no FK
  course_id uuid not null,
  youtube_video_id text not null,
  transcript_artifact_uri text not null,
  transcript_checksum text not null,
  transcript_source text not null check (transcript_source in ('captions','whisper')),
  chunk_count int not null check (chunk_count >= 0),
  ready_at timestamptz not null,
  updated_at timestamptz not null default now()
);
create index video_indexes_course_idx on ai.video_indexes (course_id);

create table ai.summaries (
  id uuid primary key,
  owner_id text not null,
  course_id uuid not null,
  video_id uuid not null,
  lesson_id uuid,
  scope text not null check (scope in ('video','lesson')),
  start_seconds numeric(10,3),
  end_seconds numeric(10,3),
  transcript_checksum text not null,
  prompt_hash text not null,
  model text not null,
  markdown text not null,
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  created_at timestamptz not null default now()
);
create unique index summaries_cache_key_idx on ai.summaries (
  owner_id, video_id, scope, coalesce(lesson_id, '00000000-0000-0000-0000-000000000000'::uuid), transcript_checksum, prompt_hash
);
create index summaries_lookup_idx on ai.summaries (owner_id, course_id, video_id);

create table ai.topic_boundaries (
  id uuid primary key,
  owner_id text not null,
  course_id uuid not null,
  video_id uuid not null,
  transcript_checksum text not null,
  prompt_hash text not null,
  model text not null,
  boundaries jsonb not null,               -- [{title,start,end,reason}]
  input_tokens int not null default 0,
  output_tokens int not null default 0,
  created_at timestamptz not null default now(),
  unique (owner_id, video_id, transcript_checksum, prompt_hash)
);

create table ai.quota_usage (
  owner_id text not null,
  quota_date date not null,
  llm_tokens_used int not null default 0,
  primary key (owner_id, quota_date)
);

create table ai.outbox (
  id uuid primary key,
  routing_key text not null,
  message jsonb not null,
  published_at timestamptz,
  created_at timestamptz not null default now()
);
create index ai_outbox_unpublished_idx on ai.outbox (created_at) where published_at is null;

create table ai.message_dedupe (
  message_id uuid primary key,
  message_type text not null,
  processed_at timestamptz not null default now()
);
```

## REST endpoints

All require `X-User-Id` from KrakenD.

### `POST /v1/qna`

Purpose: answer a User question scoped to one Video by default; Course-wide retrieval is explicit opt-in.

```python
class Citation(BaseModel):
    video_id: UUID
    chunk_index: int
    start: float
    end: float
    text: str

class QnARequest(BaseModel):
    course_id: UUID
    video_id: UUID
    question: str = Field(min_length=1)
    course_wide: bool = False
    top_k: int = Field(default=6, ge=1, le=12)

class QnAResponse(BaseModel):
    answer_markdown: str
    citations: list[Citation]
    model: str
    input_tokens: int
    output_tokens: int
```

Auth: current User; verify `ai.video_indexes.owner_id` matches. Enforce LLM token quota before LiteLLM call.

### `POST /v1/summaries`

Purpose: create or return cached Summary for a Video or Lesson.

```python
class SummaryRequest(BaseModel):
    course_id: UUID
    video_id: UUID
    lesson_id: UUID | None = None
    scope: Literal['video','lesson'] = 'video'
    start_seconds: float | None = None
    end_seconds: float | None = None
    force_refresh: bool = False

class SummaryResponse(BaseModel):
    id: UUID
    course_id: UUID
    video_id: UUID
    lesson_id: UUID | None
    scope: str
    markdown: str
    model: str
    cached: bool
    created_at: datetime
```

Auth: current User. `lesson` scope requires start/end from catalog caller or request; no cross-schema join.

### `POST /v1/topic-boundaries`

Purpose: generate natural topic boundaries for a Video so catalog can create Lessons/Days. Never produce even time cuts.

```python
class TopicBoundaryRequest(BaseModel):
    course_id: UUID
    video_id: UUID
    target_lesson_count: int | None = Field(default=None, ge=1, le=50)
    max_lesson_seconds: int | None = Field(default=None, gt=0)
    force_refresh: bool = False

class TopicBoundary(BaseModel):
    title: str
    start_seconds: float
    end_seconds: float
    reason: str

class TopicBoundaryResponse(BaseModel):
    video_id: UUID
    boundaries: list[TopicBoundary]
    model: str
    cached: bool
```

Auth: current User or internal catalog call with propagated headers. Enforce LLM quota.

### `GET /v1/videos/{video_id}/ai-status`

Purpose: tell UI/catalog whether transcript chunks are available.

```python
class AiStatusResponse(BaseModel):
    video_id: UUID
    ready: bool
    chunk_count: int
    transcript_source: Literal['captions','whisper'] | None
    updated_at: datetime | None
```

### `GET /healthz`

No auth; shallow checks DB, Weaviate, LiteLLM config.

## RabbitMQ

Exchange: `learnpilot.topic`.

### Consumes `VideoReady`

Owned by ingestion; exact producer contract in `04-ingestion-service.md`.

Queue: `ai.events.video_ready`, routing key `ingestion.event.video_ready`.

On consume:

1. dedupe by `event_id` in `ai.message_dedupe`;
2. upsert `ai.video_indexes`;
3. ack. No LLM call in the consumer.

DLX/TTL/DLQ: Phase 0 defaults with terminal `learnpilot.dlq.ai.video_ready`.

### Publishes `SummaryReady`

No current consumer; kept as the ai-owned event emitted when a Summary is newly generated.

Routing key: `ai.event.summary_ready`.

```python
class SummaryReadyPayload(BaseModel):
    owner_id: str
    summary_id: UUID
    scope: Literal['video','lesson']
    lesson_id: UUID | None = None
    transcript_checksum: str

class SummaryReadyEvent(BaseEvent):
    event_id: UUID
    event_type: Literal['SummaryReady']
    saga_id: UUID
    course_id: UUID
    video_id: UUID
    occurred_at: datetime
    payload: SummaryReadyPayload
    schema_version: Literal[1] = 1
```

`SummaryReady` is written to `ai.outbox` in the same transaction as `ai.summaries` insert. If no consumer appears, the event can be dropped before coding; current requirement asks for ai outbox/event support.

## External integrations

- Weaviate reads:
  - one `TranscriptChunk` collection;
  - default filter: `course_id == request.course_id AND video_id == request.video_id`;
  - course-wide opt-in: `course_id == request.course_id` only;
  - query embedding uses E5 `query: ` prefix.
- LiteLLM:
  - provider/model config defaults to Groq;
  - used for Summary, Q&A synthesis, topic-boundary generation;
  - token usage updates `ai.quota_usage`.
- Summary cache:
  - key includes owner, Video/Lesson scope, transcript checksum, prompt hash;
  - `force_refresh` bypasses read but stores a new row only if checksum/prompt changed.
- Topic boundaries:
  - LLM sees transcript chunks with timestamps;
  - response must be validated: starts at 0, monotonic, non-overlapping, ends <= Video duration if known;
  - reject/repair with one retry if invalid; never fall back to even cuts.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| AI-1 | Service skeleton + migrations | INF-8 | M | schema applies; health works. |
| AI-2 | VideoReady consumer | AI-1, ING-9 | M | idempotently upserts `video_indexes`. Coordination required with Vasu ingestion contract. |
| AI-3 | Weaviate retrieval helper | AI-2 | M | video-scoped and course-wide filters return timestamped chunks. |
| AI-4 | Q&A endpoint | AI-3 | L | embeds query, retrieves chunks, calls LiteLLM, returns citations, enforces token quota. |
| AI-5 | Topic-boundary endpoint | AI-3 | L | generates validated natural boundaries; no even-cut fallback. Coordination required with Moniya catalog. |
| AI-6 | Summary endpoint/cache | AI-3 | M | returns cached Summary or generates/stores new Summary and emits `SummaryReady`. |
| AI-7 | Outbox relay | AI-6 | S | ai outbox publishes events and marks `published_at`. |
| AI-8 | Tests | AI-2..7 | M | fake Weaviate/LLM covers retrieval filters, cache keys, quota, invalid boundary retry. |

## Cross-service dependencies

- Consumes ingestion-owned `VideoReady` — coordination required with Vasu's ingestion contract.
- Serves topic-boundary endpoint called by catalog-service — coordination required with Moniya.
- Reads chunks written by ingestion in Weaviate; both must share `TranscriptChunk` schema from Phase 0.

## Open questions / assumptions

- Q&A responses are not persisted in v1; add history only when product asks.
- `SummaryReady` has no consumer yet; keep only if event publication is required during implementation.
- Lesson duration for boundary validation may need catalog to pass duration if not present in ai DB.
