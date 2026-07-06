"""initial catalog schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-05

Creates the `catalog` schema, the three enum types, and all 10 tables
(sources, courses, videos, lessons, plans, days, day_lessons, progress,
outbox, message_dedupe) per docs/plans/02-catalog-service.md.

The video_id UUID is propagated from the ingestion service's VideosDiscovered
event, so videos.id has server_default=gen_random_uuid() and is also
insertable as a client-supplied value.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCHEMA = "catalog"


def upgrade() -> None:
    op.execute(f"create schema if not exists {SCHEMA}")

    # Enums first; tables reference them.
    source_type = sa.Enum(
        "video", "playlist", name="source_type", schema=SCHEMA, create_type=False
    )
    course_state = sa.Enum(
        "pending_ingestion",
        "ready",
        "failed",
        "unsupported_source",
        name="course_state",
        schema=SCHEMA,
        create_type=False,
    )
    plan_state = sa.Enum(
        "pending", "ready", "failed", name="plan_state", schema=SCHEMA, create_type=False
    )
    bind = op.get_bind()
    sa.Enum("video", "playlist", name="source_type", schema=SCHEMA).create(
        bind, checkfirst=True
    )
    sa.Enum(
        "pending_ingestion",
        "ready",
        "failed",
        "unsupported_source",
        name="course_state",
        schema=SCHEMA,
    ).create(bind, checkfirst=True)
    sa.Enum("pending", "ready", "failed", name="plan_state", schema=SCHEMA).create(
        bind, checkfirst=True
    )

    # ---- sources ----
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "original_url"),
        schema=SCHEMA,
    )
    op.create_index(
        "sources_owner_idx",
        "sources",
        ["owner_id", sa.text("created_at desc")],
        schema=SCHEMA,
    )

    # ---- courses ----
    op.create_table(
        "courses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column(
            "state",
            course_state,
            server_default=sa.text("'pending_ingestion'"),
            nullable=False,
        ),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], [f"{SCHEMA}.sources.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("source_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "courses_owner_idx",
        "courses",
        ["owner_id", sa.text("created_at desc")],
        schema=SCHEMA,
    )

    # ---- videos ----
    op.create_table(
        "videos",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("youtube_video_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "transcript_available",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], [f"{SCHEMA}.sources.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("source_id", "youtube_video_id"),
        sa.CheckConstraint("duration_seconds > 0"),
        schema=SCHEMA,
    )
    op.create_index(
        "videos_source_order_idx",
        "videos",
        ["source_id", "position"],
        schema=SCHEMA,
    )

    # ---- lessons ----
    op.create_table(
        "lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "start_seconds",
            sa.Numeric(10, 3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("end_seconds", sa.Numeric(10, 3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], [f"{SCHEMA}.courses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], [f"{SCHEMA}.videos.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("course_id", "position"),
        sa.CheckConstraint("start_seconds >= 0"),
        sa.CheckConstraint("end_seconds > start_seconds"),
        schema=SCHEMA,
    )
    op.create_index(
        "lessons_course_order_idx", "lessons", ["course_id", "position"], schema=SCHEMA
    )
    op.create_index("lessons_video_idx", "lessons", ["video_id"], schema=SCHEMA)

    # ---- plans ----
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            plan_state,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("target_days", sa.Integer(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], [f"{SCHEMA}.courses.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("mode in ('complete_in_days','manual')"),
        sa.CheckConstraint("target_days is null or target_days > 0"),
        schema=SCHEMA,
    )
    op.create_index("plans_course_idx", "plans", ["course_id"], schema=SCHEMA)

    # ---- days ----
    op.create_table(
        "days",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], [f"{SCHEMA}.plans.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("plan_id", "day_index"),
        sa.CheckConstraint("day_index > 0"),
        schema=SCHEMA,
    )

    # ---- day_lessons (composite PK) ----
    op.create_table(
        "day_lessons",
        sa.Column("day_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["day_id"], [f"{SCHEMA}.days.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("day_id", "lesson_id"),
        sa.UniqueConstraint("day_id", "position"),
        schema=SCHEMA,
    )

    # ---- progress (composite PK) ----
    op.create_table(
        "progress",
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "watched_seconds",
            sa.Numeric(10, 3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"], [f"{SCHEMA}.lessons.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("owner_id", "lesson_id"),
        sa.CheckConstraint("watched_seconds >= 0"),
        schema=SCHEMA,
    )
    op.create_index(
        "progress_owner_updated_idx",
        "progress",
        ["owner_id", sa.text("updated_at desc")],
        schema=SCHEMA,
    )

    # ---- outbox ----
    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("routing_key", sa.Text(), nullable=False),
        sa.Column("message", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "catalog_outbox_unpublished_idx",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at is null"),
        schema=SCHEMA,
    )

    # ---- message_dedupe ----
    op.create_table(
        "message_dedupe",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("message_id"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("message_dedupe", schema=SCHEMA)
    op.drop_index("catalog_outbox_unpublished_idx", table_name="outbox", schema=SCHEMA)
    op.drop_table("outbox", schema=SCHEMA)
    op.drop_index("progress_owner_updated_idx", table_name="progress", schema=SCHEMA)
    op.drop_table("progress", schema=SCHEMA)
    op.drop_table("day_lessons", schema=SCHEMA)
    op.drop_table("days", schema=SCHEMA)
    op.drop_index("plans_course_idx", table_name="plans", schema=SCHEMA)
    op.drop_table("plans", schema=SCHEMA)
    op.drop_index("lessons_video_idx", table_name="lessons", schema=SCHEMA)
    op.drop_index("lessons_course_order_idx", table_name="lessons", schema=SCHEMA)
    op.drop_table("lessons", schema=SCHEMA)
    op.drop_index("videos_source_order_idx", table_name="videos", schema=SCHEMA)
    op.drop_table("videos", schema=SCHEMA)
    op.drop_index("courses_owner_idx", table_name="courses", schema=SCHEMA)
    op.drop_table("courses", schema=SCHEMA)
    op.drop_index("sources_owner_idx", table_name="sources", schema=SCHEMA)
    op.drop_table("sources", schema=SCHEMA)

    sa.Enum(name="source_type", schema=SCHEMA).drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="course_state", schema=SCHEMA).drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="plan_state", schema=SCHEMA).drop(op.get_bind(), checkfirst=True)

    op.execute(f"drop schema if exists {SCHEMA} cascade")
