"""Cross-service RabbitMQ event/command contracts (Pydantic v2).

Producers and consumers in different services import from here.
Field names, types, and routing keys ARE the wire contract — do not
mutate them without coordinating across services.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyUrl, BaseModel, ConfigDict


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    saga_id: UUID
    course_id: UUID
    video_id: UUID | None = None
    occurred_at: datetime
    payload: dict[str, Any]


class BaseEvent(Envelope):
    event_id: UUID
    event_type: str


class BaseCommand(Envelope):
    command_id: UUID
    command_type: str


# ---- catalog OWNS, ingestion consumes ----

class IngestSourcePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    owner_id: str
    source_id: UUID
    source_url: AnyUrl
    source_type_hint: Literal["video", "playlist", "unknown"] = "unknown"


class IngestSourceCommand(BaseCommand):
    model_config = ConfigDict(extra="ignore")
    command_type: Literal["IngestSource"] = "IngestSource"
    video_id: None = None
    routing_key: Literal["catalog.command.ingest_source"] = "catalog.command.ingest_source"
    payload: IngestSourcePayload


# ---- ingestion OWNS, catalog/ai consume ----

class DiscoveredVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    video_id: UUID
    youtube_video_id: str
    canonical_url: AnyUrl
    title: str
    duration_seconds: int
    thumbnail_url: AnyUrl | None = None
    position: int


class VideosDiscoveredPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    owner_id: str
    source_id: UUID
    videos: list[DiscoveredVideo]


class VideosDiscoveredEvent(BaseEvent):
    model_config = ConfigDict(extra="ignore")
    event_type: Literal["VideosDiscovered"] = "VideosDiscovered"
    video_id: None = None
    payload: VideosDiscoveredPayload


class VideoReadyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    owner_id: str
    source_id: UUID
    youtube_video_id: str
    transcript_artifact_uri: str
    transcript_checksum: str
    transcript_source: Literal["captions", "whisper"]
    chunk_count: int


class VideoReadyEvent(BaseEvent):
    model_config = ConfigDict(extra="ignore")
    event_type: Literal["VideoReady"] = "VideoReady"
    payload: VideoReadyPayload


class CourseReadyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    owner_id: str
    source_id: UUID
    state: Literal[
        "ready",
        "unsupported_source",
        "transcript_unavailable",
        "source_fetch_failed",
        "failed",
    ]
    failure_reason: str | None = None


class CourseReadyEvent(BaseEvent):
    model_config = ConfigDict(extra="ignore")
    event_type: Literal["CourseReady"] = "CourseReady"
    video_id: None = None
    payload: CourseReadyPayload


ROUTING_KEY_BY_EVENT = {
    "VideosDiscovered": "ingestion.event.videos_discovered",
    "VideoReady": "ingestion.event.video_ready",
    "CourseReady": "ingestion.event.course_ready",
}
