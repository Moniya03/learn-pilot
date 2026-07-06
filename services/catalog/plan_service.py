"""Plan CRUD + worker body (CAT-6).

All write paths commit their own transactions. Read paths return dicts so
the route can validate against PlanDetailResponse. `generate_plan` is the
worker's per-plan unit of work: idempotent, owns its own session, marks
state='failed' on any ai-service error per the fail-gracefully decision.
"""
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.session import AsyncSessionLocal
from models import Course, Day, DayLesson, Lesson, Plan, PlanState, Progress, Video
from plan_schemas import CreatePlanRequest
from repository import progress_percent

_log = logging.getLogger(__name__)

# ponytail: short timeout; ai-service topic-boundaries is a per-video LLM call
_AI_TIMEOUT = 10.0


# ---- helpers ----


async def _get_owned_course(
    s: AsyncSession, course_id: UUID, owner_id: str
) -> Course | None:
    return (
        await s.execute(
            select(Course).where(
                Course.id == course_id, Course.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()


def _plan_dict(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "course_id": plan.course_id,
        "state": plan.state.value,
        "mode": plan.mode,
        "target_days": plan.target_days,
        "starts_on": plan.starts_on,
    }


def _lesson_dict(lesson: Lesson, watched: float | None) -> dict[str, Any]:
    watched_f = float(watched) if watched is not None else 0.0
    return {
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


# ---- POST: manual ----


async def create_manual_plan(
    s: AsyncSession,
    owner_id: str,
    course_id: UUID,
    body: CreatePlanRequest,
) -> Plan | None:
    """Returns Plan on success; None when course is missing/not owned (caller 404s).

    Raises ValueError on validation failures (caller 422s).
    """
    course = await _get_owned_course(s, course_id, owner_id)
    if course is None:
        return None

    if not body.manual_days or any(len(day) == 0 for day in body.manual_days):
        raise ValueError("manual_days required and each day must have at least one lesson")

    wanted = {lid for day in body.manual_days for lid in day}
    found = set(
        (
            await s.execute(
                select(Lesson.id).where(
                    Lesson.id.in_(wanted), Lesson.course_id == course_id
                )
            )
        ).scalars().all()
    )
    if found != wanted:
        missing = wanted - found
        raise ValueError(f"manual_days contains lesson id(s) not in this course: {sorted(missing)}")

    plan = Plan(
        id=uuid4(),
        owner_id=owner_id,
        course_id=course_id,
        state=PlanState.ready,
        mode="manual",
        target_days=None,
        starts_on=body.starts_on,
    )
    s.add(plan)

    for i, lesson_ids in enumerate(body.manual_days, start=1):
        day = Day(
            id=uuid4(),
            owner_id=owner_id,
            plan_id=plan.id,
            day_index=i,
            planned_date=None,  # ponytail: manual days have no planned_date
        )
        s.add(day)
        for pos, lid in enumerate(lesson_ids, start=1):
            s.add(DayLesson(day_id=day.id, lesson_id=lid, position=pos))

    await s.commit()
    return plan


# ---- POST: auto (complete_in_days) ----


async def create_auto_plan(
    s: AsyncSession,
    owner_id: str,
    course_id: UUID,
    body: CreatePlanRequest,
) -> Plan | None:
    """Returns Plan with state='pending' (worker picks it up); None on 404."""
    course = await _get_owned_course(s, course_id, owner_id)
    if course is None:
        return None

    plan = Plan(
        id=uuid4(),
        owner_id=owner_id,
        course_id=course_id,
        state=PlanState.pending,
        mode="complete_in_days",
        target_days=body.target_days,
        starts_on=body.starts_on,
    )
    s.add(plan)
    await s.commit()
    return plan


# ---- GET: detail ----


async def get_plan_detail(
    s: AsyncSession, plan_id: UUID, owner_id: str
) -> dict[str, Any] | None:
    """Plan + ordered days + ordered lessons with progress_percent.

    Returns None if plan is missing or its course is not owned by owner_id.
    """
    plan = (
        await s.execute(
            select(Plan)
            .join(Course, Course.id == Plan.course_id)
            .where(Plan.id == plan_id, Course.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    if plan is None:
        return None

    stmt = (
        select(Day, DayLesson, Lesson, Progress.watched_seconds)
        .join(DayLesson, DayLesson.day_id == Day.id)
        .join(Lesson, Lesson.id == DayLesson.lesson_id)
        .outerjoin(
            Progress,
            (Progress.lesson_id == Lesson.id) & (Progress.owner_id == owner_id),
        )
        .where(Day.plan_id == plan_id)
        .order_by(Day.day_index, DayLesson.position)
    )

    days_by_id: dict[UUID, dict[str, Any]] = {}
    order: list[UUID] = []
    for day, _dl, lesson, watched in (await s.execute(stmt)).all():
        if day.id not in days_by_id:
            days_by_id[day.id] = {
                "id": day.id,
                "day_index": day.day_index,
                "planned_date": day.planned_date,
                "lessons": [],
            }
            order.append(day.id)
        days_by_id[day.id]["lessons"].append(_lesson_dict(lesson, watched))

    return {**_plan_dict(plan), "days": [days_by_id[did] for did in order]}


# ---- worker support ----


async def list_pending_auto_plans(s: AsyncSession) -> list[Plan]:
    return list(
        (
            await s.execute(
                select(Plan).where(
                    Plan.state == PlanState.pending,
                    Plan.mode == "complete_in_days",
                )
            )
        ).scalars().all()
    )


def _chunk_lessons(lessons: list[Lesson], target_days: int) -> list[list[Lesson]]:
    """Fair split: spread remainder to the first few days. Empty days allowed when N < target_days."""
    n = len(lessons)
    if n == 0 or target_days <= 0:
        return []
    base, extra = divmod(n, target_days)
    out: list[list[Lesson]] = []
    cursor = 0
    for i in range(target_days):
        size = base + (1 if i < extra else 0)
        out.append(lessons[cursor : cursor + size])
        cursor += size
    return out


async def _fetch_boundaries(
    plan: Plan, videos: list[Video]
) -> list[list[dict[str, Any]]] | None:
    """Call ai-service /v1/topic-boundaries per video. Returns None on any failure."""
    url = f"{settings.AI_SERVICE_URL.rstrip('/')}/v1/topic-boundaries"
    headers = {"X-User-Id": plan.owner_id, "X-User-Email": ""}
    out: list[list[dict[str, Any]]] = []
    async with httpx.AsyncClient(timeout=_AI_TIMEOUT) as client:
        for v in videos:
            try:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={
                        "course_id": str(plan.course_id),
                        "video_id": str(v.id),
                        "target_lesson_count": None,
                        "max_lesson_seconds": None,
                        "force_refresh": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:  # ponytail: any failure = fail this plan
                _log.warning("ai topic-boundaries failed for video %s: %s", v.id, e)
                return None
            out.append(data.get("boundaries", []))
    return out


async def generate_plan(plan_id: UUID) -> None:
    """Worker body for ONE plan. Idempotent: cleans partial state, regenerates.

    On any ai-service error (or no videos, or zero boundaries) sets state='failed'
    and commits; outer loop in plan_worker continues with the next plan.
    """
    async with AsyncSessionLocal() as s:
        plan = (
            await s.execute(select(Plan).where(Plan.id == plan_id))
        ).scalar_one_or_none()
        if plan is None:
            return
        if plan.state != PlanState.pending or plan.mode != "complete_in_days":
            return  # another worker / state transition; skip
        if not plan.target_days or plan.target_days <= 0:
            _log.error("plan %s has no target_days; marking failed", plan_id)
            plan.state = PlanState.failed
            await s.commit()
            return

        course = (
            await s.execute(select(Course).where(Course.id == plan.course_id))
        ).scalar_one_or_none()
        if course is None:
            plan.state = PlanState.failed
            await s.commit()
            return

        videos = list(
            (
                await s.execute(
                    select(Video)
                    .where(Video.source_id == course.source_id)
                    .order_by(Video.position)
                )
            ).scalars().all()
        )
        if not videos:
            plan.state = PlanState.failed
            await s.commit()
            return

        # Idempotency: clean prior partial work. DayLesson cascades from both sides.
        await s.execute(delete(Day).where(Day.plan_id == plan_id))
        await s.execute(delete(Lesson).where(Lesson.course_id == plan.course_id))

        boundaries_by_video = await _fetch_boundaries(plan, videos)
        if boundaries_by_video is None:
            plan.state = PlanState.failed
            await s.commit()
            return

        # Positions must be unique within the course; assign sequentially across all videos.
        new_lessons: list[Lesson] = []
        next_pos = 1
        for v, boundaries in zip(videos, boundaries_by_video):
            for b in boundaries:
                new_lessons.append(
                    Lesson(
                        id=uuid4(),
                        owner_id=plan.owner_id,
                        course_id=plan.course_id,
                        video_id=v.id,
                        title=b["title"],
                        position=next_pos,
                        start_seconds=b["start_seconds"],
                        end_seconds=b["end_seconds"],
                    )
                )
                next_pos += 1

        if not new_lessons:
            plan.state = PlanState.failed
            await s.commit()
            return

        for l in new_lessons:
            s.add(l)

        # Distribute lessons across target_days. Empty days are valid (more days than lessons).
        chunks = _chunk_lessons(new_lessons, plan.target_days)
        for i, chunk in enumerate(chunks, start=1):
            day = Day(
                id=uuid4(),
                owner_id=plan.owner_id,
                plan_id=plan.id,
                day_index=i,
                planned_date=(
                    plan.starts_on + timedelta(days=i - 1)
                    if plan.starts_on
                    else None
                ),
            )
            s.add(day)
            for pos, lesson in enumerate(chunk, start=1):
                s.add(DayLesson(day_id=day.id, lesson_id=lesson.id, position=pos))

        plan.state = PlanState.ready
        await s.commit()
        _log.info(
            "plan %s generated: %d lessons across %d days",
            plan_id,
            len(new_lessons),
            plan.target_days,
        )
