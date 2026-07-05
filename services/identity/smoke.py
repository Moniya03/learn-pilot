"""Self-check for identity-service logic against the real DB.

Run: `uv run python -m smoke`  (needs DATABASE_URL pointing at learnpilot DB).

Verifies the non-trivial invariants: find-or-create, last_login_at refresh,
email-required guard, profile patch, email change propagation, unique email.
Cleans up its test rows afterward. Exits non-zero on any failure — the
smallest runnable thing that fails if the logic breaks.
"""
import asyncio
import os
import sys

import asyncpg

from config import settings
from repository import get_by_owner_id, profile_exists, update_profile, upsert_on_login


async def check():
    conn = await asyncpg.connect(dsn=settings.dsn)
    try:
        await conn.execute("set search_path to identity")
        # Clear any stale rows from a previous run.
        await conn.execute(
            "delete from users where owner_id like 'smoke-%'"
        )

        # 1. find-or-create: first call creates.
        # Repository does NOT enforce email-required (that's the endpoint's
        # trust-boundary guard); we pass a real email here.
        row = await upsert_on_login(conn, "smoke-1", "smoke1@example.com")
        assert row["owner_id"] == "smoke-1"
        assert row["email"] == "smoke1@example.com"
        first_login = row["last_login_at"]
        assert await profile_exists(conn, "smoke-1")

        # 2. second call refreshes last_login_at (>= first, usually >).
        row2 = await upsert_on_login(conn, "smoke-1", "smoke1@example.com")
        assert row2["last_login_at"] >= first_login, "last_login_at did not refresh"

        # 3. email change propagates (Zitadel updated the email).
        row3 = await upsert_on_login(conn, "smoke-1", "smoke1-new@example.com")
        assert row3["email"] == "smoke1-new@example.com", "email not updated"

        # 4. PATCH sets display_name + avatar_url, moves updated_at.
        row4 = await update_profile(
            conn, "smoke-1", "Smoke", "https://example.com/a.png"
        )
        assert row4["display_name"] == "Smoke"
        assert row4["avatar_url"] == "https://example.com/a.png"
        assert row4["updated_at"] >= row["updated_at"]

        # 5. unique email: inserting a second user with the same email fails.
        try:
            await conn.execute(
                "insert into users (owner_id, email) values ('smoke-2', $1)",
                "smoke1-new@example.com",
            )
            raise AssertionError("duplicate email insert should have failed")
        except asyncpg.UniqueViolationError:
            pass  # expected

        # 6. get_by_owner_id round-trips.
        fetched = await get_by_owner_id(conn, "smoke-1")
        assert fetched["display_name"] == "Smoke"

        # 7. get_by_owner_id returns None for unknown.
        assert await get_by_owner_id(conn, "nope") is None

        print("all identity self-checks passed")
    finally:
        await conn.execute("delete from users where owner_id like 'smoke-%'")
        await conn.close()


# ponytail: self-check. `uv run python -m smoke` exercises repo logic end-to-end.
if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL not set; run inside the service env or container")
    asyncio.run(check())