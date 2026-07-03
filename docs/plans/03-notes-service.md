Owner: Moniya

# notes-service plan

Purpose: own Notebook and Note CRUD for markdown blocks scoped to a Course and optionally anchored to a Lesson/time.

Dependencies: catalog-service for Course/Lesson ownership validation, identity via `owner_id`. Public prefix: `/api/notes`.

## DB schema: `notes`

```sql
create table notes.notebooks (
  id uuid primary key,
  owner_id text not null,                  -- User reference, no FK
  course_id uuid not null,                 -- catalog Course reference, no FK
  title text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, course_id)
);
create index notebooks_owner_idx on notes.notebooks (owner_id, updated_at desc);

create table notes.notes (
  id uuid primary key,
  owner_id text not null,
  notebook_id uuid not null references notes.notebooks(id) on delete cascade,
  course_id uuid not null,                 -- copied for fast owner/course filtering
  lesson_id uuid,                          -- catalog Lesson reference, no FK
  video_timestamp numeric(10,3) check (video_timestamp is null or video_timestamp >= 0),
  markdown text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index notes_notebook_updated_idx on notes.notes (notebook_id, updated_at desc);
create index notes_course_idx on notes.notes (owner_id, course_id, updated_at desc);
create index notes_lesson_idx on notes.notes (owner_id, lesson_id, video_timestamp);
```

Notes are discrete markdown blocks, not one giant markdown file.

## REST endpoints

All require `X-User-Id` from KrakenD.

### `GET /v1/courses/{course_id}/notebook`

Purpose: get or create the Notebook for a Course.

```python
class NotebookResponse(BaseModel):
    id: UUID
    owner_id: str
    course_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
```

Auth: current User; validate Course ownership via catalog-service or trust catalog route if already verified. Shortest safe path: call catalog-service once on create.

### `GET /v1/courses/{course_id}/notes`

Query: `lesson_id: UUID | None`, `limit: int=100`, `cursor: str | None`.

Response: `list[NoteResponse]`.

### `POST /v1/courses/{course_id}/notes`

Purpose: create a markdown Note.

```python
class CreateNoteRequest(BaseModel):
    markdown: str = Field(min_length=1)
    lesson_id: UUID | None = None
    video_timestamp: float | None = Field(default=None, ge=0)

class NoteResponse(BaseModel):
    id: UUID
    notebook_id: UUID
    course_id: UUID
    lesson_id: UUID | None
    video_timestamp: float | None
    markdown: str
    created_at: datetime
    updated_at: datetime
```

Auth: current User owns Course; if `lesson_id` present, validate Lesson belongs to Course via catalog-service.

### `PATCH /v1/notes/{note_id}`

```python
class UpdateNoteRequest(BaseModel):
    markdown: str | None = Field(default=None, min_length=1)
    lesson_id: UUID | None = None
    video_timestamp: float | None = Field(default=None, ge=0)
```

Response: `NoteResponse`.

Auth: current User owns Note.

### `DELETE /v1/notes/{note_id}`

Response: `204 No Content`.

Auth: current User owns Note.

### `GET /healthz`

No auth.

## RabbitMQ

None for v1. Course deletion note cleanup can be synchronous through `DELETE /courses/{course_id}` only if requested later; otherwise cascade is manual/product decision.

## External integrations

- catalog-service: validate Course/Lesson ownership on Note create/update. Propagate trusted headers through KrakenD.
- Markdown rendering is frontend-only. Backend stores raw markdown.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| NOTE-1 | Service skeleton + migration | INF-8 | S | schema applies; health works. |
| NOTE-2 | Catalog validation client | NOTE-1, CAT-2 | S | helper verifies Course and Lesson ownership through catalog API. |
| NOTE-3 | Notebook get/create | NOTE-2 | M | one Notebook per User/Course. |
| NOTE-4 | Note CRUD | NOTE-3 | M | create/list/update/delete enforce owner_id. |
| NOTE-5 | Anchoring validation | NOTE-4 | S | anchored Note requires Lesson in Course; timestamp non-negative. |
| NOTE-6 | Tests | NOTE-5 | S | owner isolation, markdown storage, Lesson anchor validation covered. |

## Cross-service dependencies

- Calls catalog-service for Course/Lesson ownership validation — coordination required with Moniya's catalog implementation.

## Open questions / assumptions

- No full-text search in v1; add Postgres `tsvector` later only if users need Note search.
- Note version history is deferred.
