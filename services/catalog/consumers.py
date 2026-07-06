"""Ingestion event consumers for catalog-service.

Queues follow Phase 0 DLQ convention `learnpilot.dlq.<queue>` — the plan
phrases it as `learnpilot.dlq.catalog.*`; the queue literal goes here.
"""
import logging
from datetime import datetime, timezone
from uuid import uuid4

import aio_pika
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from db.session import AsyncSessionLocal
from models import (
    Course,
    CourseState,
    Lesson,
    MessageDedupe,
    Video,
)
from shared.events import (
    CourseReadyEvent,
    VideosDiscoveredEvent,
)


_log = logging.getLogger(__name__)


QUEUE_VIDEOS_DISCOVERED = "catalog.events.videos_discovered"
QUEUE_COURSE_READY = "catalog.events.course_ready"
RK_VIDEOS_DISCOVERED = "ingestion.event.videos_discovered"
RK_COURSE_READY = "ingestion.event.course_ready"
DLQ_VIDEOS_DISCOVERED = "learnpilot.dlq.catalog.videos_discovered"
DLQ_COURSE_READY = "learnpilot.dlq.catalog.course_ready"


# ---- state mapping (CourseReady.payload.state -> CourseState enum) ----

_STATE_MAP: dict[str, CourseState] = {
    "ready": CourseState.ready,
    "unsupported_source": CourseState.unsupported_source,
    "transcript_unavailable": CourseState.failed,
    "source_fetch_failed": CourseState.failed,
    "failed": CourseState.failed,
}


async def _dedupe(message_id, message_type: str) -> bool:
    """Insert into message_dedupe. Returns True if this is a new event;
    False if the message_id was already processed (ack without work)."""
    async with AsyncSessionLocal() as s:
        try:
            s.add(MessageDedupe(message_id=message_id, message_type=message_type))
            await s.commit()
            return True
        except IntegrityError:
            await s.rollback()
            return False


async def on_videos_discovered(message: aio_pika.IncomingMessage) -> None:
    """Upsert Videos (id = propagated UUID) + create whole-video Lessons."""
    async with message.process():  # ack on success, nack on exception
        event = VideosDiscoveredEvent.model_validate_json(message.body)
        if not await _dedupe(event.event_id, "VideosDiscovered"):
            return  # already processed; ack and bail

        p = event.payload
        async with AsyncSessionLocal() as s:
            for v in p.videos:
                await s.execute(
                    pg_insert(Video)
                    .values(
                        id=v.video_id,  # propagated UUID — one ID across services
                        owner_id=p.owner_id,
                        source_id=p.source_id,
                        youtube_video_id=v.youtube_video_id,
                        title=v.title,
                        duration_seconds=v.duration_seconds,
                        thumbnail_url=str(v.thumbnail_url) if v.thumbnail_url else None,
                        position=v.position,
                    )
                    .on_conflict_do_update(
                        index_elements=[Video.source_id, Video.youtube_video_id],
                        set_={
                            "title": v.title,
                            "duration_seconds": v.duration_seconds,
                            "thumbnail_url": str(v.thumbnail_url) if v.thumbnail_url else None,
                            "position": v.position,
                        },
                    )
                )
                await s.execute(
                    pg_insert(Lesson)
                    .values(
                        id=uuid4(),
                        owner_id=p.owner_id,
                        course_id=event.course_id,
                        video_id=v.video_id,
                        title=v.title,
                        position=v.position,
                        start_seconds=0,
                        end_seconds=v.duration_seconds,
                    )
                    .on_conflict_do_update(
                        index_elements=[Lesson.course_id, Lesson.position],
                        set_={
                            "title": v.title,
                            "start_seconds": 0,
                            "end_seconds": v.duration_seconds,
                            "video_id": v.video_id,
                        },
                    )
                )
            await s.commit()


async def on_course_ready(message: aio_pika.IncomingMessage) -> None:
    """Idempotently update Course state from CourseReady event.

    Plain UPDATE — a missing course is logged and acked (don't block the
    queue on a stale terminal event). No INSERT side-effect.
    """
    async with message.process():
        event = CourseReadyEvent.model_validate_json(message.body)
        if not await _dedupe(event.event_id, "CourseReady"):
            return

        p = event.payload
        new_state = _STATE_MAP.get(p.state, CourseState.failed)
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as s:
            result = await s.execute(
                update(Course)
                .where(Course.id == event.course_id)
                .values(state=new_state, failure_reason=p.failure_reason, updated_at=now)
            )
            await s.commit()
            if result.rowcount == 0:
                _log.warning(
                    "CourseReady for unknown course %s; acking stale terminal event",
                    event.course_id,
                )
