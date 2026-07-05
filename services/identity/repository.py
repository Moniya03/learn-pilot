"""User repository: find-or-create, profile update, lookup.

Email from KrakenD is treated as verified (Zitadel/Google handle verification,
per plan assumptions).
"""
import asyncpg


async def upsert_on_login(
    conn: asyncpg.Connection,
    owner_id: str,
    email: str | None,
) -> dict:
    """Find-or-create a User on /me access; refresh email + last_login_at."""
    row = await conn.fetchrow(
        """
        insert into users (owner_id, email, last_login_at)
        values ($1, $2, now())
        on conflict (owner_id) do update
          set email        = excluded.email,
              last_login_at = now()
        returning owner_id, email, display_name, avatar_url,
                  created_at, updated_at, last_login_at
        """,
        owner_id,
        email,
    )
    return dict(row)


async def update_profile(
    conn: asyncpg.Connection,
    owner_id: str,
    display_name: str | None,
    avatar_url: str | None,
) -> dict | None:
    """Patch display_name / avatar_url for the current User."""
    row = await conn.fetchrow(
        """
        update users
           set display_name = coalesce($2, display_name),
               avatar_url   = $3,
               updated_at   = now()
         where owner_id = $1
        returning owner_id, email, display_name, avatar_url,
                  created_at, updated_at, last_login_at
        """,
        owner_id,
        display_name,
        avatar_url,
    )
    return dict(row) if row else None


async def profile_exists(conn: asyncpg.Connection, owner_id: str) -> bool:
    return await conn.fetchval(
        "select exists(select 1 from users where owner_id = $1)", owner_id
    )


async def get_by_owner_id(
    conn: asyncpg.Connection, owner_id: str
) -> dict | None:
    row = await conn.fetchrow(
        """select owner_id, email, display_name, avatar_url,
                  created_at, updated_at, last_login_at
             from users where owner_id = $1""",
        owner_id,
    )
    return dict(row) if row else None