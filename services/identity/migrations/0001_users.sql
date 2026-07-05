-- identity-service schema: identity.users
-- Run against the identity schema (search_path = identity).
-- owner_id = Zitadel sub, the canonical User id. No cross-schema FKs.

create table if not exists users (
  owner_id       text primary key,
  email          text        not null,
  display_name   text,
  avatar_url     text,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  last_login_at timestamptz
);

create unique index if not exists users_email_idx on users (lower(email));