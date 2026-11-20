"""Pydantic schemas for mobile device registration."""

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class RegisterMobileDeviceRequest(BaseModel):
    """Request payload for POST /api/v1/mobile/devices."""

    model_config = ConfigDict(extra="forbid")

    fcm_token: str = Field(..., min_length=1, max_length=512)
    platform: str = Field(default="android", min_length=1, max_length=32)
    device_name: str | None = Field(default=None, min_length=1, max_length=128)
    app_version: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("fcm_token")
    @classmethod
    def validate_fcm_token(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fcm_token must not be blank")
        if value != value.strip():
            raise ValueError("fcm_token must not have leading or trailing whitespace")
        return value

    @field_validator("platform", mode="before")
    @classmethod
    def normalize_platform(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                raise ValueError("platform must not be blank")
            return normalized
        return value

    @field_validator("device_name", "app_version", mode="before")
    @classmethod
    def normalize_optional_metadata(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{info.field_name} must not be blank")
            return normalized
        return value


class RegisterMobileDeviceResponse(BaseModel):
    """Device metadata returned after registration; excludes the FCM token and owner IDs."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    platform: str
    device_name: Optional[str] = None
    app_version: Optional[str] = None
    is_active: bool
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
