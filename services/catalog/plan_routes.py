"""Plan REST endpoints (CAT-6): POST create, GET detail.

All routes (except /healthz) require X-User-Id from KrakenD. Non-owners
get 404 on reads/mutations — we don't leak plan/course existence.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from plan_schemas import CreatePlanRequest, PlanDetailResponse, PlanResponse
from plan_service import create_auto_plan, create_manual_plan, get_plan_detail
from shared.auth import CurrentUser, current_user

router = APIRouter(prefix="/v1")


@router.post(
    "/courses/{course_id}/plans",
    status_code=status.HTTP_201_CREATED,
    response_model=PlanResponse,
)
async def post_plan(
    course_id: UUID,
    body: CreatePlanRequest,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    if body.mode == "manual":
        try:
            plan = await create_manual_plan(s, user.owner_id, course_id, body)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
            )
    else:  # complete_in_days
        if body.target_days is None or body.target_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="target_days required and must be >0 for complete_in_days",
            )
        plan = await create_auto_plan(s, user.owner_id, course_id, body)

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="course not found"
        )
    return plan  # PlanResponse.from_attributes=True reads the Plan ORM directly


@router.get(
    "/plans/{plan_id}",
    response_model=PlanDetailResponse,
)
async def get_plan(
    plan_id: UUID,
    user: CurrentUser = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    detail = await get_plan_detail(s, plan_id, user.owner_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="plan not found"
        )
    return detail
