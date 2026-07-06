"""Async repository functions for catalog-service.

All write paths commit their own transactions. Read paths don't need to —
the session is yielded by get_session() and lives for the request.
"""
import base64
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Course, CourseState, Lesson, Outbox, Progress, Source, SourceType, Video
from shared.events import IngestSourceCommand, IngestSourcePayload


# ---- progress math (lesson range, capped 0..100) ----


def progress_percent(watched: float, start: float, end: float) -> float:
    dur = float(end) - float(start)
    if dur <= 0:
        return 0.0
    return max(0.0, min(100.0, float(watched) / dur * 100.0))


# ---- source-type detection (cheap URL sniff, no yt-dlp) ----


def _source_type(url: str) -> SourceType:
    return SourceType.playlist if "playlist" in url else SourceType.video


# ---- transactional outbox: Source + Course + Outbox in ONE commit ----


async def create_source_and_course(
    s: AsyncSession,
    owner_id: str,
    source_url: str,
    title: str | None,
) -> Course:
    source_id = uuid4()
    course_id = uuid4()
    saga_id = uuid4()
    command_id = uuid4()  # reused as outbox.id for traceability

    s.add(
        Source(
            id=source_id,
            owner_id=owner_id,
            source_type=_source_type(source_url),
            original_url=source_url,
            title=title,
        )
    )
    s.add(
        Course(
            id=course_id,
            owner_id=owner_id,
            source_id=source_id,
            title=title or source_url,  # ponytail: Course.title is NOT NULL
            state=CourseState.pending_ingestion,
        )
    )
    await s.flush()  # populate server-default created_at/updated_at

    cmd = IngestSourceCommand(
        command_id=command_id,
        saga_id=saga_id,
        course_id=course_id,
        occurred_at=datetime.now(timezone.utc),
        payload=IngestSourcePayload(
            owner_id=owner_id,
            source_id=source_id,
            source_url=source_url,
            source_type_hint="playlist" if "playlist" in source_url else "video",
        ),
    )
    s.add(
        Outbox(
            id=command_id,
            routing_key=cmd.routing_key,
            message=cmd.model_dump(mode="json"),
        )
    )
    await s.commit()
    return await get_course(s, course_id)  # type: ignore[return-value]


# ---- list / read ----


def _encode_cursor(created_at: datetime) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"created_at": created_at.isoformat()}).encode()
    ).decode()


def _decode_cursor(cursor: str) -> datetime | None:
    try:
        return datetime.fromisoformat(
            json.loads(base64.urlsafe_b64decode(cursor).decode())["created_at"]
        )
    except Exception:
        return None


async def list_courses(
    s: AsyncSession,
    owner_id: str,
    state: CourseState | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> list[Course]:
    """Owner filter + optional state; keyset on created_at desc.

    Returns up to `limit` rows. No has_more envelope in the public spec —
    caller paginates by passing the last row's created_at back as cursor.
    """
    stmt = select(Course).where(Course.owner_id == owner_id)
    if state is not None:
        stmt = stmt.where(Course.state == state)
    if cursor:
        dt = _decode_cursor(cursor)
        if dt is not None:
            stmt = stmt.where(Course.created_at < dt)
    stmt = stmt.order_by(Course.created_at.desc(), Course.id.desc()).limit(limit)
    return list((await s.execute(stmt)).scalars().all())


async def get_course(s: AsyncSession, course_id: UUID) -> Course | None:
    return (
        await s.execute(select(Course).where(Course.id == course_id))
    ).scalar_one_or_none()


async def get_course_detail(
    s: AsyncSession,
    course_id: UUID,
    owner_id: str,
) -> dict[str, Any] | None:
    """Single SELECT with outerjoin: Course, Lesson, Progress (filtered by owner).

    Returns None if the course is missing OR not owned by owner_id — caller
    maps both to 404 (don't leak existence to non-owners).
    """
    stmt = (
        select(Course, Lesson, Progress.watched_seconds)
        .outerjoin(Lesson, Lesson.course_id == Course.id)
        .outerjoin(
            Progress,
            (Progress.lesson_id == Lesson.id) & (Progress.owner_id == owner_id),
        )
        .where(Course.id == course_id, Course.owner_id == owner_id)
        .order_by(Lesson.position)
    )
    rows = (await s.execute(stmt)).all()
    if not rows:
        return None
    course: Course = rows[0][0]
    lessons: list[dict[str, Any]] = []
    for _c, lesson, watched in rows:
        if lesson is None:
            continue  # course has no lessons yet (pending_ingestion)
        watched_f = float(watched) if watched is not None else 0.0
        lessons.append(
            {
                "id": lesson.id,
                "video_id": lesson.video_id,
                "title": lesson.title,
                "position": lesson.position,
                "start_seconds": float(lesson.start_seconds),
                "end_seconds": float(lesson.end_seconds),
                "progress_percent": progress_percent(
                    watched_f, lesson.start_seconds, lesson.end_seconds
                ),
            }
        )
    return {
        "id": course.id,
        "source_id": course.source_id,
        "owner_id": course.owner_id,
        "title": course.title,
        "state": course.state.value,
        "failure_reason": course.failure_reason,
        "created_at": course.created_at,
        "updated_at": course.updated_at,
        "lessons": lessons,
    }


async def get_course_lessons(
    s: AsyncSession,
    course_id: UUID,
    owner_id: str,
) -> list[dict[str, Any]]:
    """Lessons for the player UI, with progress_percent for the given owner.

    Caller must have already verified course ownership via get_course.
    """
    stmt = (
        select(Lesson, Progress.watched_seconds)
        .outerjoin(
            Progress,
            (Progress.lesson_id == Lesson.id) & (Progress.owner_id == owner_id),
        )
        .where(Lesson.course_id == course_id)
        .order_by(Lesson.position)
    )
    out: list[dict[str, Any]] = []
    for lesson, watched in (await s.execute(stmt)).all():
        watched_f = float(watched) if watched is not None else 0.0
        out.append(
            {
                "id": lesson.id,
                "video_id": lesson.video_id,
                "title": lesson.title,
                "position": lesson.position,
                "start_seconds": float(lesson.start_seconds),
                "end_seconds": float(lesson.end_seconds),
                "progress_percent": progress_percent(
                    watched_f, lesson.start_seconds, lesson.end_seconds
                ),
            }
        )
    return out


# ---- ownership + delete ----


async def delete_course(
    s: AsyncSession, course_id: UUID, owner_id: str
) -> bool:
    """Returns True if a course was deleted; False if missing or not owned.

    FK ondelete=CASCADE removes lessons/videos/progress/etc. No outbox
    publish — the plan marks delete cleanup events as TBD.
    """
    course = await get_course(s, course_id)
    if course is None or course.owner_id != owner_id:
        return False
    await s.execute(delete(Course).where(Course.id == course_id))
    await s.commit()
    return True


# ---- progress (high-write path) ----


async def get_lesson_for_progress(
    s: AsyncSession, lesson_id: UUID, owner_id: str
) -> tuple[Lesson, Video] | None:
    """Verify the lesson's course is owned by owner_id. Returns (Lesson, Video)
    on success; caller passes the Lesson to upsert_progress to avoid a
    second read on this hot path.
    """
    stmt = (
        select(Lesson, Video)
        .join(Video, Video.id == Lesson.video_id)
        .join(Course, Course.id == Lesson.course_id)
        .where(Lesson.id == lesson_id, Course.owner_id == owner_id)
    )
    row = (await s.execute(stmt)).first()
    if row is None:
        return None
    return (row[0], row[1])


async def upsert_progress(
    s: AsyncSession,
    owner_id: str,
    lesson_id: UUID,
    watched_seconds: float,
    lesson: Lesson,
) -> dict[str, Any]:
    """Single-row upsert into progress (PK = owner_id, lesson_id).

    No extra reads: ownership was checked and the Lesson was fetched by
    get_lesson_for_progress. watched_seconds may exceed (end-start) when a
    user scrubs past the end; we cap the percent but store the actual value.
    """
    now = datetime.now(timezone.utc)
    await s.execute(
        pg_insert(Progress)
        .values(
            owner_id=owner_id,
            lesson_id=lesson_id,
            watched_seconds=watched_seconds,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[Progress.owner_id, Progress.lesson_id],
            set_={"watched_seconds": watched_seconds, "updated_at": now},
        )
    )
    await s.commit()
    return {
        "lesson_id": lesson_id,
        "watched_seconds": float(watched_seconds),
        "progress_percent": progress_percent(
            watched_seconds, lesson.start_seconds, lesson.end_seconds
        ),
        "updated_at": now,
    }
