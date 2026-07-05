"""identity-service — owns the local User profile keyed by Zitadel sub.

Routes (all behind KrakenD except /healthz):
  GET   /healthz              DB connectivity check
  GET   /v1/me                find-or-create current User, return profile
  PATCH /v1/me                update local display fields
  GET   /v1/users/{owner_id}  lookup for owner display
"""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, status

from config import settings
from db import pool_factory
from repository import get_by_owner_id, profile_exists, update_profile, upsert_on_login
from schemas import UpdateMeRequest, UserResponse
from shared.auth import CurrentUser, current_user


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.pool = await pool_factory()
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(title="identity-service", lifespan=lifespan)


def _require_email(user: CurrentUser) -> str:
    """Email is always present from KrakenD (verified email claim). Refuse if
    the gateway misconfigured and dropped it — input validation at the trust
    boundary, not a 500 from the DB NOT NULL constraint."""
    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-User-Email required from KrakenD",
        )
    return str(user.email)


@app.get("/healthz")
async def healthz() -> dict:
    """DB connectivity check; no auth."""
    async with app.state.pool.acquire() as conn:
        val = await conn.fetchval("select 1")
    return {"status": "ok", "db": val == 1, "schema": settings.DB_SCHEMA}


@app.get("/v1/me", response_model=UserResponse)
async def me(user: CurrentUser = Depends(current_user)) -> UserResponse:
    """Find-or-create the current User; refresh last_login_at on every call."""
    email = _require_email(user)
    async with app.state.pool.acquire() as conn:
        row = await upsert_on_login(conn, user.owner_id, email)
    return UserResponse.model_validate(row)


@app.patch("/v1/me", response_model=UserResponse)
async def patch_me(
    body: UpdateMeRequest,
    user: CurrentUser = Depends(current_user),
) -> UserResponse:
    """Update local display fields only; creates the User if /me never ran."""
    async with app.state.pool.acquire() as conn:
        if not await profile_exists(conn, user.owner_id):
            await upsert_on_login(conn, user.owner_id, _require_email(user))
        row = await update_profile(
            conn,
            user.owner_id,
            body.display_name,
            str(body.avatar_url) if body.avatar_url else None,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return UserResponse.model_validate(row)


@app.get("/v1/users/{owner_id}", response_model=UserResponse)
async def get_user(
    owner_id: str, user: CurrentUser = Depends(current_user)
) -> UserResponse:
    """Lookup by owner_id for display; authenticated, internal-ish for now."""
    async with app.state.pool.acquire() as conn:
        row = await get_by_owner_id(conn, owner_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return UserResponse.model_validate(row)