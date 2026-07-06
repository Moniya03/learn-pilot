"""REST endpoints for catalog-service (prefix /v1).

All routes (except /healthz) require X-User-Id from KrakenD. Non-owners
get 404 on reads/mutations — we don't leak course existence to other users.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from models import CourseState
from quota import quota_allows_course_create
from repository import (
    create_source_and_course,
    delete_course,
    get_course,
    get_course_detail,
    get_course_lessons,
    get_lesson_for_progress,
    list_courses,
    upsert_progress,
)
from schemas import (
    CourseDetailResponse,
    CourseResponse,
    CreateCourseRequest,
    LessonResponse,
    ProgressResponse,
    UpsertProgressRequest,
)
from shared.auth import CurrentUser, current_user

router = APIRouter(prefix="/v1")


# ---- POST /v1/courses ----


@router.post(
    "/courses",
    status_code=status.HTTP_201_CREATED,
    response_model=CourseResponse,
)
async def post_course(
    body: CreateCourseRequest,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    if not await quota_allows_course_create(s, user.owner_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="daily course limit exceeded",
        )
    try:
        course = await create_source_and_course(
            s, user.owner_id, str(body.source_url), body.title
        )
    except IntegrityError:
        # (owner_id, original_url) unique on sources — duplicate URL.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source already added",
        )
    return course  # FastAPI validates via CourseResponse + from_attributes


# ---- GET /v1/courses ----


@router.get("/courses", response_model=list[CourseResponse])
async def get_courses(
    state: str | None = None,
    limit: int = Query(50, le=200),
    cursor: str | None = None,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    state_enum: CourseState | None = None
    if state is not None:
        try:
            state_enum = CourseState(state)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid state: {state}",
            )
    rows = await list_courses(
        s, user.owner_id, state=state_enum, limit=limit, cursor=cursor
    )
    return rows


# ---- GET /v1/courses/{course_id} ----


@router.get(
    "/courses/{course_id}",
    response_model=CourseDetailResponse,
)
async def get_course_detail_route(
    course_id: UUID,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    detail = await get_course_detail(s, course_id, user.owner_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="course not found"
        )
    return detail


# ---- DELETE /v1/courses/{course_id} ----


@router.delete(
    "/courses/{course_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_course_route(
    course_id: UUID,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    if not await delete_course(s, course_id, user.owner_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="course not found"
        )


# ---- GET /v1/courses/{course_id}/lessons ----


@router.get(
    "/courses/{course_id}/lessons",
    response_model=list[LessonResponse],
)
async def get_course_lessons_route(
    course_id: UUID,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    course = await get_course(s, course_id)
    if course is None or course.owner_id != user.owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="course not found"
        )
    return await get_course_lessons(s, course_id, user.owner_id)


# ---- PUT /v1/lessons/{lesson_id}/progress ----


@router.put(
    "/lessons/{lesson_id}/progress",
    response_model=ProgressResponse,
)
async def put_progress(
    lesson_id: UUID,
    body: UpsertProgressRequest,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    result = await get_lesson_for_progress(s, lesson_id, user.owner_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="lesson not found"
        )
    lesson, _video = result
    return await upsert_progress(
        s, user.owner_id, lesson_id, body.watched_seconds, lesson
    )
