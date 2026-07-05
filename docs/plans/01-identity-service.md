Owner: Moniya

# identity-service plan

Purpose: own the local `User` profile keyed by Zitadel `sub`; find-or-create the `User` on login/profile access.

Dependencies: Phase 0 `shared.current_user`, KrakenD trusted headers, Zitadel. Public prefix: `/api/identity`.

## DB schema: `identity`

```sql
create table identity.users (
  owner_id text primary key,                 -- Zitadel sub, canonical User id
  email text not null,
  display_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_login_at timestamptz
);

create unique index users_email_idx on identity.users (lower(email));
```

No cross-schema FKs. Other services store `owner_id` only and resolve User details through this service.

## REST endpoints

All routes require `X-User-Id` from KrakenD via `current_user`.

### `GET /v1/me`

Purpose: find-or-create current `User`, return profile.

Request: none.

```python
class UserResponse(BaseModel):
    owner_id: str
    email: EmailStr
    display_name: str | None
    avatar_url: AnyUrl | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None
```

Auth: any authenticated User.

### `PATCH /v1/me`

Purpose: update local display fields only.

```python
class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    avatar_url: AnyUrl | None = None
```

Response: `UserResponse`.

Auth: current User only.

### `GET /v1/users/{owner_id}`

Purpose: internal/client lookup for owner display.

Response: `UserResponse`.

Auth: authenticated; later restrict to internal scope if needed.

### `GET /healthz`

No auth; DB connectivity check.

## RabbitMQ

None.

## External integrations

- Zitadel is not called at request time. KrakenD injects `X-User-Id` and `X-User-Email`.
- identity-service stores no Zitadel, Google, access, or refresh tokens.

## Task breakdown

| ID | Task | Depends | Size | Definition of Done |
|---|---|---:|:---:|---|
| ID-1 ✅ | Service skeleton | INF-8 | S | FastAPI app imports shared config/auth; `/healthz` works. |
| ID-2 ✅ | Migration | ID-1 | S | `identity.users` exists with indexes; idempotent `migrate.py` on startup. |
| ID-3 ✅ | Repository | ID-2 | S | upsert/find by `owner_id`, update profile, lookup by `owner_id`, profile_exists. |
| ID-4 ✅ | `GET /me` find-or-create | ID-3 | M | first request creates User from headers; repeat request updates `last_login_at`; email required (422 if missing). |
| ID-5 ✅ | Profile update/lookup endpoints | ID-4 | S | PATCH and GET by owner_id return typed responses; PATCH creates User if /me never ran. |
| ID-6 ✅ | Tests | ID-5 | S | `smoke.py` — 7-invariant self-check against real DB (find-or-create, last_login refresh, email change, PATCH, unique email, lookup, unknown→None). Run with `uv run python -m smoke`. |

## Cross-service dependencies

- catalog, notes, ingestion, ai store `owner_id` but do not FK or join to `identity.users`.
- Coordinate with Moniya's Phase 0 KrakenD config so `X-User-Email` is always present when available.

## Open questions / assumptions

- `email` is treated as verified because Zitadel/Google handles verification.
- User deletion/export is deferred until explicitly required.

## Implementation notes (2026-07-04)

All tasks complete. Key decisions and deviations:

- **DB access: raw `asyncpg`, no ORM.** Justified for identity (1 table, no
  joins, transactional outbox makes explicit SQL clearer than a unit-of-work
  model). Catalog, notes, and ingestion each have richer schemas and may
  benefit from SQLAlchemy 2.x async + Alembic — make that call per-service
  when implementing. Rule: if a service has ≤2 tables and no joins, use
  raw asyncpg; otherwise reach for SQLAlchemy. See the top-level README note
  that left this open.
- **Per-service `migrate.py`** instead of a centralized runner. Each service's
  Dockerfile runs `python -m migrate` on startup — schema + DDL applied
  idempotently. Simpler than orchestrating cross-service migration ordering;
  the centralized `infra/migrations/run.py` in the Phase 0 plan still exists
  as a future option.
- **`shared/` is path-imported**, not pip-installed. No wheel, no build step.
  Each service Dockerfile copies `shared/` and sets `PYTHONPATH=/app`.
  Services list `fastapi`, `pydantic`, etc. as their own deps (they need them
  anyway). This avoids the path-dependency version-skew dance inside Docker.
- **Email is required at the trust boundary.** The plan's schema has
  `email text not null`, but `X-User-Email` could be absent if KrakenD is
  misconfigured. Rather than store `''` (which breaks `EmailStr` validation
  in the response), the endpoint rejects with 422 when email is missing.
  The plan says email is always present from KrakenD — this enforces that
  contract explicitly instead of letting a DB-level NOT NULL violation
  become a 500.
- **No DB roles** (plan INF-3). For solo dev on a shared Postgres, schema
  isolation via `search_path` is enough. Per-schema roles are YAGNI until
  multi-team or production deployment.
- **The POST route in KrakenD** for `/api/identity/{rest}` exists but
  identity-service has no POST endpoints. Harmless; stays because KrakenD
  config blocks are shared across services and identity may grow a create
  endpoint later (e.g., admin-invited users).
