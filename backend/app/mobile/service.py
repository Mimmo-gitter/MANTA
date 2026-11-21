"""Persistence helpers for mobile device registration."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import MobileDevice, User
from app.mobile.schemas import RegisterMobileDeviceRequest, RegisterMobileDeviceResponse


class MobileUserNotFoundError(LookupError):
    """Raised when the authenticated JWT principal has no active user row in the tenant."""


async def resolve_platform_user_id(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    sub: str | None = None,
    email: str | None = None,
) -> uuid.UUID | None:
    """Resolve the internal `users.id` UUID for an authenticated principal within a tenant.

    Matches by `google_sub` (Keycloak subject), case-insensitive `email`, or direct `users.id`
    (for tokens where `sub` / `user_id` already equals the DB primary key).
    """
    conditions = []
    priority_branches = []

    clean_sub = sub.strip() if isinstance(sub, str) and sub.strip() else None
    clean_email = email.strip().lower() if isinstance(email, str) and email.strip() else None

    if clean_sub is not None:
        conditions.append(User.google_sub == clean_sub)
        priority_branches.append((User.google_sub == clean_sub, 0))
    if clean_email is not None:
        conditions.append(func.lower(User.email) == clean_email)
        priority_branches.append((func.lower(User.email) == clean_email, 1))
    if user_id is not None:
        conditions.append(User.id == user_id)
        priority_branches.append((User.id == user_id, 2))

    if not conditions:
        return None

    statement = (
        select(User.id)
        .where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            or_(*conditions),
        )
        .order_by(case(*priority_branches, else_=3))
        .limit(1)
    )
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def resolve_or_create_mobile_device(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    request: RegisterMobileDeviceRequest,
    *,
    user_sub: str | None = None,
    user_email: str | None = None,
) -> RegisterMobileDeviceResponse:
    """Create or refresh this user's registration for the supplied FCM token."""
    resolved_user_id = user_id
    if user_sub is not None or user_email is not None or resolved_user_id is None:
        resolved_user_id = await resolve_platform_user_id(
            db,
            tenant_id,
            user_id=user_id,
            sub=user_sub,
            email=user_email,
        )
        if resolved_user_id is None:
            raise MobileUserNotFoundError(
                "Authenticated user is not provisioned in the application database for this tenant."
            )

    now = datetime.now(timezone.utc)
    statement = (
        pg_insert(MobileDevice)
        .values(
            tenant_id=tenant_id,
            user_id=resolved_user_id,
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
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    fcm_token: str,
    *,
    user_sub: str | None = None,
    user_email: str | None = None,
) -> bool:
    """Removes this user's registration for the supplied FCM token."""
    resolved_user_id = user_id
    if user_sub is not None or user_email is not None or resolved_user_id is None:
        resolved_user_id = await resolve_platform_user_id(
            db,
            tenant_id,
            user_id=user_id,
            sub=user_sub,
            email=user_email,
        )
        if resolved_user_id is None:
            return False

    statement = (
        delete(MobileDevice)
        .where(MobileDevice.tenant_id == tenant_id)
        .where(MobileDevice.user_id == resolved_user_id)
        .where(MobileDevice.fcm_token == fcm_token)
        .returning(MobileDevice.id)
    )
    result = await db.execute(statement)
    return result.scalar_one_or_none() is not None
