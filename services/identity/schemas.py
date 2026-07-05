"""Pydantic response/request models for identity-service."""
from datetime import datetime

from pydantic import AnyUrl, BaseModel, EmailStr, Field


class UserResponse(BaseModel):
    owner_id: str
    email: EmailStr
    display_name: str | None
    avatar_url: AnyUrl | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    avatar_url: AnyUrl | None = None