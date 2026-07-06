"""Self-check for catalog-service logic against the real DB.

Run: `uv run python -m smoke`  (needs DATABASE_URL pointing at learnpilot DB).

Verifies the DoD invariants for CAT-8: progress-percent cap math, consumer
dedupe gate, outbox fetch+mark cycle, per-owner course/progress isolation
(the data-layer half of endpoint ownership). Cleans up its test rows
afterward. Exits non-zero on any failure — the smallest runnable thing
that fails if the logic breaks.

Mirrors services/identity/smoke.py in shape: raw asyncpg, no SQLAlchemy or
FastAPI imports at module top, so it ast-parses and `python -m smoke` runs
inside the container even when those deps aren't installed locally.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg


# ponytail: inline copy of repository.progress_percent so this module is
# runnable without SQLAlchemy. Keep in sync with repository.progress_percent.
def _progress_percent(watched: float, start: float, end: float) -> float:
    dur = float(end) - float(start)
    if dur <= 0:
        return 0.0
    return max(0.0, min(100.0, float(watched) / dur * 100.0))


def _dsn() -> str:
    url = os.environ["DATABASE_URL"]
    # asyncpg.connect doesn't accept the +asyncpg driver suffix.
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def check():
    conn = await asyncpg.connect(dsn=_dsn())
    try:
        await conn.execute("set search_path to catalog, public")

        # Clear any stale rows from a previous run.
        smoke_ids: dict[str, str] = {"dedupe_mid": "", "outbox_id": ""}
        await _cleanup(conn, smoke_ids)

        # 1. progress math: 0/50/100 caps + negative + scrub-past-end + zero-dur.
        assert _progress_percent(0, 0, 100) == 0.0
        assert _progress_percent(50, 0, 100) == 50.0
        assert _progress_percent(100, 0, 100) == 100.0
        assert _progress_percent(150, 0, 100) == 100.0  # watched > dur -> cap
        assert _progress_percent(-10, 0, 100) == 0.0    # negative -> clamp
        assert _progress_percent(50, 100, 100) == 0.0   # end == start -> 0

        # 2. consumer dedupe gate: message_dedupe.message_id is PK.
        mid = uuid4()
        smoke_ids["dedupe_mid"] = str(mid)
        await conn.execute(
            "insert into message_dedupe (message_id, message_type) values ($1, $2)",
            mid, "videos_discovered",
        )
        try:
            await conn.execute(
                "insert into message_dedupe (message_id, message_type) values ($1, $2)",
                mid, "videos_discovered",
            )
            raise AssertionError("duplicate message_dedupe insert should have failed")
        except asyncpg.UniqueViolationError:
            pass  # expected — the dedupe gate holds

        # 3. outbox fetch+mark cycle (mimics outbox_relay.fetch_unpublished +
        # mark_published). The relay uses ORDER BY created_at LIMIT 100 with
        # a partial index on published_at IS NULL.
        oid = uuid4()
        smoke_ids["outbox_id"] = str(oid)
        await conn.execute(
            "insert into outbox (id, routing_key, message) values ($1, $2, $3::jsonb)",
            oid, "catalog.command.ingest_source", '{"command_id": "x"}',
        )
        unpublished = await conn.fetch(
            "select id from outbox where id = $1 and published_at is null", oid
        )
        assert len(unpublished) == 1, "outbox row should be unpublished"

        await conn.execute(
            "update outbox set published_at = $1 where id = $2",
            datetime.now(timezone.utc), oid,
        )
        still_unpub = await conn.fetch(
            "select id from outbox where id = $1 and published_at is null", oid
        )
        assert len(still_unpub) == 0, "marked outbox row must not resurface"

        # 4. per-owner isolation at the data layer: routes filter by owner_id;
        # verify a non-owner's lookup returns nothing and one's own progress
        # doesn't bleed across owners on the same lesson.
        owner_a = "smoke-A"
        owner_b = "smoke-B"
        sid = uuid4()
        cid = uuid4()
        vid = uuid4()
        lid = uuid4()

        await conn.execute(
            "insert into sources (id, owner_id, source_type, original_url, title) "
            "values ($1, $2, 'video', $3, $4)",
            sid, owner_a, f"https://example.com/{sid}", "smoke src",
        )
        await conn.execute(
            "insert into courses (id, owner_id, source_id, title) "
            "values ($1, $2, $3, $4)",
            cid, owner_a, sid, "smoke course",
        )
        await conn.execute(
            "insert into videos (id, owner_id, source_id, youtube_video_id, "
            "title, duration_seconds, position) "
            "values ($1, $2, $3, $4, $5, $6, $7)",
            vid, owner_a, sid, f"yt-{vid}", "smoke video", 600, 0,
        )
        await conn.execute(
            "insert into lessons (id, owner_id, course_id, video_id, title, "
            "position, start_seconds, end_seconds) "
            "values ($1, $2, $3, $4, $5, $6, $7, $8)",
            lid, owner_a, cid, vid, "smoke lesson", 0, 0, 100,
        )

        # owner_b querying owner_a's course must see nothing (404 in route).
        other = await conn.fetchrow(
            "select id from courses where id = $1 and owner_id = $2", cid, owner_b
        )
        assert other is None, "owner_b must not see owner_a's course"

        # owner_a logs 50% progress.
        await conn.execute(
            "insert into progress (owner_id, lesson_id, watched_seconds) "
            "values ($1, $2, $3)",
            owner_a, lid, 50,
        )
        row = await conn.fetchrow(
            "select watched_seconds from progress "
            "where owner_id = $1 and lesson_id = $2",
            owner_a, lid,
        )
        assert row is not None
        assert _progress_percent(float(row["watched_seconds"]), 0, 100) == 50.0

        # owner_b writes different progress on the same lesson — owner_a's
        # row must not change (the upsert is keyed on owner_id, lesson_id).
        await conn.execute(
            "insert into progress (owner_id, lesson_id, watched_seconds) "
            "values ($1, $2, $3) on conflict (owner_id, lesson_id) do update "
            "set watched_seconds = excluded.watched_seconds",
            owner_b, lid, 99,
        )
        a_after = await conn.fetchrow(
            "select watched_seconds from progress "
            "where owner_id = $1 and lesson_id = $2",
            owner_a, lid,
        )
        b_row = await conn.fetchrow(
            "select watched_seconds from progress "
            "where owner_id = $1 and lesson_id = $2",
            owner_b, lid,
        )
        assert float(a_after["watched_seconds"]) == 50.0, (
            "owner_a's progress must not be touched by owner_b's write"
        )
        assert float(b_row["watched_seconds"]) == 99.0

        print("all catalog self-checks passed")
    finally:
        await _cleanup(conn, smoke_ids)
        await conn.close()


async def _cleanup(conn: asyncpg.Connection, smoke_ids: dict[str, str]) -> None:
    """Delete every smoke-* row. Order respects FK cascade direction.

    message_dedupe and outbox rows are deleted by their exact smoke id, not by
    type/time, so a crashed prior run or real consumer rows are never touched.
    """
    await conn.execute("delete from day_lessons where day_id in "
                       "(select id from days where owner_id like 'smoke-%')")
    await conn.execute("delete from days where owner_id like 'smoke-%'")
    await conn.execute("delete from plans where owner_id like 'smoke-%'")
    await conn.execute("delete from progress where owner_id like 'smoke-%'")
    await conn.execute("delete from lessons where owner_id like 'smoke-%'")
    await conn.execute("delete from videos where owner_id like 'smoke-%'")
    await conn.execute("delete from courses where owner_id like 'smoke-%'")
    await conn.execute("delete from sources where owner_id like 'smoke-%'")
    # outbox + message_dedupe have no owner_id; delete only the exact smoke
    # row ids we generated. A crash mid-run leaves at most one orphan, which
    # the next run's `oid`/`mid` won't match — acceptable, never wipes real rows.
    if smoke_ids.get("outbox_id"):
        await conn.execute(
            "delete from outbox where id = $1", smoke_ids["outbox_id"]
        )
    if smoke_ids.get("dedupe_mid"):
        await conn.execute(
            "delete from message_dedupe where message_id = $1",
            smoke_ids["dedupe_mid"],
        )


# ponytail: self-check. `uv run python -m smoke` exercises DoD logic end-to-end.
if __name__ == "__main__":
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL not set; run inside the service env or container")
    asyncio.run(check())
