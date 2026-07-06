"""Pydantic v2 request/response models for catalog-service REST endpoints.

Field-for-field from docs/plans/02-catalog-service.md — these ARE the wire
contract for the public prefix. ORM rows are validated via
`from_attributes=True`; LessonResponse is built from explicit dicts in
repository.get_course_lessons/get_course_detail.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict, Field


class CreateCourseRequest(BaseModel):
    source_url: AnyUrl
    title: str | None = None


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID
    owner_id: str
    title: str
    state: Literal["pending_ingestion", "ready", "failed", "unsupported_source"]
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class LessonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    video_id: UUID
    title: str
    position: int
    start_seconds: float
    end_seconds: float
    progress_percent: float


class CourseDetailResponse(CourseResponse):
    lessons: list[LessonResponse]


class UpsertProgressRequest(BaseModel):
    watched_seconds: float = Field(ge=0)


class ProgressResponse(BaseModel):
    lesson_id: UUID
    watched_seconds: float
    progress_percent: float
    updated_at: datetime
