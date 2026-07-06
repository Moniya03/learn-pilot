"""Cheap pre-check quota helper.

CAT-7: obvious over-limit Course creation is rejected BEFORE the outbox insert.
Actual heavy-op quotas (ingestion/AI) live in their own services. This is a
24-hour rolling window per owner; the limit is product config, not hard-coded.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import Course


async def quota_allows_course_create(s: AsyncSession, owner_id: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = select(func.count()).select_from(Course).where(
        Course.owner_id == owner_id, Course.created_at >= cutoff
    )
    count = (await s.execute(stmt)).scalar_one()
    return int(count) < settings.COURSE_CREATE_DAILY_LIMIT
