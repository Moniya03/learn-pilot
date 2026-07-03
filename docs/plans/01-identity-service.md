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
| ID-1 | Service skeleton | INF-8 | S | FastAPI app imports shared config/auth; `/healthz` works. |
| ID-2 | Migration | ID-1 | S | `identity.users` exists with indexes. |
| ID-3 | Repository | ID-2 | S | upsert/find by `owner_id`, update profile, lookup by `owner_id`. |
| ID-4 | `GET /me` find-or-create | ID-3 | M | first request creates User from headers; repeat request updates `last_login_at`. |
| ID-5 | Profile update/lookup endpoints | ID-4 | S | PATCH and GET by owner_id return typed responses. |
| ID-6 | Tests | ID-5 | S | header-auth test, find-or-create test, no-token-storage assertion. |

## Cross-service dependencies

- catalog, notes, ingestion, ai store `owner_id` but do not FK or join to `identity.users`.
- Coordinate with Moniya's Phase 0 KrakenD config so `X-User-Email` is always present when available.

## Open questions / assumptions

- `email` is treated as verified because Zitadel/Google handles verification.
- User deletion/export is deferred until explicitly required.
