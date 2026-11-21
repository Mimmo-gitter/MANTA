"""Persistence helpers for mobile device registration."""

from datetime import datetime, timezone
import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete

from app.db.models.models import MobileDevice
from app.mobile.schemas import RegisterMobileDeviceRequest, RegisterMobileDeviceResponse

async def resolve_or_create_mobile_device(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    request: RegisterMobileDeviceRequest,
) -> RegisterMobileDeviceResponse:
    """Create or refresh this user's registration for the supplied FCM token."""
    now = datetime.now(timezone.utc)
    statement = (
        pg_insert(MobileDevice)
        .values(
            tenant_id=tenant_id,
            user_id=user_id,
            fcm_token=request.fcm_token,
            platform=request.platform,
            device_name=request.device_name,
            app_version=request.app_version,
            is_active=True,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_tenant_user_fcm_token",
            set_={
                "platform": request.platform,
                "device_name": request.device_name,
                "app_version": request.app_version,
                "is_active": True,
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        .returning(MobileDevice)
    )
    result = await db.execute(statement)
    mobile_device = result.scalar_one()
    return RegisterMobileDeviceResponse.model_validate(mobile_device)

async def delete_mobile_device(
    db: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, fcm_token: str
) -> bool:
    """Removes this user's registration for the supplied FCM token."""
    statement = (
        delete(MobileDevice)
        .where(MobileDevice.tenant_id == tenant_id)
        .where(MobileDevice.user_id == user_id)
        .where(MobileDevice.fcm_token == fcm_token)
        .returning(MobileDevice.id)
    )
    result = await db.execute(statement)
    return result.scalar_one_or_none() is not None
