"""Pydantic v2 schemas for plan endpoints (CAT-6)."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ponytail: reuse LessonResponse from schemas.py so DayResponse.lessons matches the course-detail contract
from schemas import LessonResponse


class CreatePlanRequest(BaseModel):
    mode: Literal["complete_in_days", "manual"]
    target_days: int | None = Field(default=None, gt=0)
    starts_on: date | None = None
    manual_days: list[list[UUID]] | None = None  # lesson ids per Day, manual only


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    course_id: UUID
    state: Literal["pending", "ready", "failed"]
    mode: str
    target_days: int | None
    starts_on: date | None


class DayResponse(BaseModel):
    id: UUID
    day_index: int
    planned_date: date | None
    lessons: list[LessonResponse]


class PlanDetailResponse(PlanResponse):
    days: list[DayResponse]
