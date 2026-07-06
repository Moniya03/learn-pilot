"""SQLAlchemy 2.x ORM models for the catalog schema.

All tables live in schema `catalog` (ADR-0011). The DB is the source of truth;
columns here mirror the SQL DDL in docs/plans/02-catalog-service.md.
Plain Column style — fewer lines than Mapped[] for this many tables, equally valid.
"""
import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    pass


# ---- enums (mirror Postgres type names: source_type / course_state / plan_state) ----


class SourceType(str, enum.Enum):
    video = "video"
    playlist = "playlist"


class CourseState(str, enum.Enum):
    pending_ingestion = "pending_ingestion"
    ready = "ready"
    failed = "failed"
    unsupported_source = "unsupported_source"


class PlanState(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


# Common shorthand for `__table_args__ = (..., {"schema": ...})`.
_SCHEMA = {"schema": settings.DB_SCHEMA}


# ---- sources ----


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("owner_id", "original_url"),
        Index("sources_owner_idx", "owner_id", text("created_at desc")),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(Text, nullable=False)
    source_type = Column(
        Enum(SourceType, name="source_type", native_enum=True, create_type=False),
        nullable=False,
    )
    original_url = Column(Text, nullable=False)
    canonical_url = Column(Text)
    title = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- courses ----


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("source_id"),
        Index("courses_owner_idx", "owner_id", text("created_at desc")),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(Text, nullable=False)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(Text, nullable=False)
    description = Column(Text)
    thumbnail_url = Column(Text)
    state = Column(
        Enum(CourseState, name="course_state", native_enum=True, create_type=False),
        nullable=False,
        server_default=text("'pending_ingestion'"),
    )
    failure_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- videos ----


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("source_id", "youtube_video_id"),
        Index("videos_source_order_idx", "source_id", "position"),
        CheckConstraint("duration_seconds > 0"),
        _SCHEMA,
    )

    # consumer path: ingestion supplies video_id directly (propagated UUID).
    # create-source path may omit it; gen_random_uuid() fills in. Either works.
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_id = Column(Text, nullable=False)
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    youtube_video_id = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    thumbnail_url = Column(Text)
    position = Column(Integer, nullable=False)
    transcript_available = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- lessons ----


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint("course_id", "position"),
        Index("lessons_course_order_idx", "course_id", "position"),
        Index("lessons_video_idx", "video_id"),
        CheckConstraint("start_seconds >= 0"),
        CheckConstraint("end_seconds > start_seconds"),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(Text, nullable=False)
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    video_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(Text, nullable=False)
    position = Column(Integer, nullable=False)
    start_seconds = Column(Numeric(10, 3), nullable=False, server_default=text("0"))
    end_seconds = Column(Numeric(10, 3), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- plans ----


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        Index("plans_course_idx", "course_id"),
        CheckConstraint("mode in ('complete_in_days','manual')"),
        CheckConstraint("target_days is null or target_days > 0"),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(Text, nullable=False)
    course_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    state = Column(
        Enum(PlanState, name="plan_state", native_enum=True, create_type=False),
        nullable=False,
        server_default=text("'pending'"),
    )
    mode = Column(Text, nullable=False)
    target_days = Column(Integer)
    starts_on = Column(Date)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- days ----


class Day(Base):
    __tablename__ = "days"
    __table_args__ = (
        UniqueConstraint("plan_id", "day_index"),
        CheckConstraint("day_index > 0"),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    owner_id = Column(Text, nullable=False)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_index = Column(Integer, nullable=False)
    planned_date = Column(Date)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- day_lessons (composite PK) ----


class DayLesson(Base):
    __tablename__ = "day_lessons"
    __table_args__ = (
        PrimaryKeyConstraint("day_id", "lesson_id"),
        UniqueConstraint("day_id", "position"),
        _SCHEMA,
    )

    day_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.days.id", ondelete="CASCADE"),
        nullable=False,
    )
    lesson_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.lessons.id", ondelete="CASCADE"),
        nullable=False,
    )
    position = Column(Integer, nullable=False)


# ---- progress (composite PK) ----


class Progress(Base):
    __tablename__ = "progress"
    __table_args__ = (
        PrimaryKeyConstraint("owner_id", "lesson_id"),
        Index("progress_owner_updated_idx", "owner_id", text("updated_at desc")),
        CheckConstraint("watched_seconds >= 0"),
        _SCHEMA,
    )

    owner_id = Column(Text, primary_key=True)
    lesson_id = Column(
        UUID(as_uuid=True),
        ForeignKey("catalog.lessons.id", ondelete="CASCADE"),
        primary_key=True,
    )
    watched_seconds = Column(Numeric(10, 3), nullable=False, server_default=text("0"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- outbox ----


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # Partial index: unpublished only. Replicated as a raw expression.
        Index(
            "catalog_outbox_unpublished_idx",
            "created_at",
            postgresql_where=text("published_at is null"),
        ),
        _SCHEMA,
    )

    id = Column(UUID(as_uuid=True), primary_key=True)
    routing_key = Column(Text, nullable=False)
    message = Column(JSONB, nullable=False)
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


# ---- message_dedupe ----


class MessageDedupe(Base):
    __tablename__ = "message_dedupe"
    __table_args__ = (_SCHEMA,)

    message_id = Column(UUID(as_uuid=True), primary_key=True)
    message_type = Column(Text, nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
