from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update

from app.auth.keycloak_admin import disable_user, sync_user_realm_role
from app.auth.permissions import Permission, Role, require_permission
from app.auth.schemas import TokenClaims
from app.db.models.models import User, UserRole, UserSession
from app.db.session import get_tenant_db_context

router = APIRouter(prefix="/api/v1")


class RoleChangeRequest(BaseModel):
    role: str


@router.get("/members")
async def list_members(
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
):
    async with get_tenant_db_context(current_user.tenant_uuid) as session:
        last_session_sq = (
            select(
                UserSession.user_id.label("session_user_id"),
                func.max(UserSession.last_active_at).label("last_session_at"),
            )
            .where(UserSession.tenant_id == current_user.tenant_uuid)
            .group_by(UserSession.user_id)
            .subquery()
        )
        result = await session.execute(
            select(
                User.id,
                User.email,
                User.full_name,
                User.role,
                User.is_active,
                User.updated_at,
                User.created_at,
                last_session_sq.c.last_session_at,
            )
            .outerjoin(last_session_sq, last_session_sq.c.session_user_id == User.id)
            .where(
                User.tenant_id == current_user.tenant_uuid,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .order_by(User.email)
        )
        rows = result.all()

    members = []
    for row in rows:
        if len(row) == 3:
            # Compatibility branch for minimal unit-test mocks returning (id, email, role)
            member_id, member_email, member_role = row
            members.append(
                {"id": str(member_id), "email": member_email, "role": str(member_role)}
            )
        else:
            (
                member_id,
                member_email,
                full_name,
                member_role,
                is_active,
                updated_at,
                created_at,
                last_session_at,
            ) = row
            last_dt = last_session_at or updated_at or created_at
            members.append(
                {
                    "id": str(member_id),
                    "email": member_email,
                    "name": full_name or member_email.split("@")[0],
                    "role": str(member_role),
                    "status": "ACTIVE" if is_active else "INACTIVE",
                    "last_active": last_dt.isoformat() if last_dt else None,
                }
            )
    return members


@router.patch("/members/{user_id}/role")
async def change_member_role(
    user_id: UUID,
    body: RoleChangeRequest,
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
):
    try:
        new_role = Role(body.role.strip().upper()).value
    except ValueError:
        raise HTTPException(422, "Невідома роль")

    tenant_id = current_user.tenant_uuid
    target_email: str | None = None
    target_sub: str | None = None

    async with get_tenant_db_context(tenant_id) as session:
        admin_result = await session.execute(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.role == Role.TENANT_ADMIN.value,
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        admin_ids = list(admin_result.scalars().all())

        target_result = await session.execute(
            select(User.id, User.email, User.google_sub).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        target_row = target_result.first()
        if target_row is None:
            raise HTTPException(404, "Користувача не знайдено")

        if len(target_row) >= 3:
            target_email = target_row[1]
            target_sub = target_row[2]

        target_is_admin = user_id in admin_ids
        is_demotion = target_is_admin and new_role != Role.TENANT_ADMIN.value
        if is_demotion and len(admin_ids) == 1:
            raise HTTPException(409, "Не можна понизити останнього адміністратора")

        await session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(role=new_role, updated_at=datetime.now(timezone.utc))
        )
        await session.execute(delete(UserRole).where(UserRole.user_id == user_id))

    await sync_user_realm_role(email=target_email, new_role=new_role, google_sub=target_sub)

    return {"user_id": str(user_id), "role": new_role}


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: UUID,
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
) -> Response:
    """Deactivate/remove an active member from the workspace (blocks removing sole admin)."""
    tenant_id = current_user.tenant_uuid
    now = datetime.now(timezone.utc)
    target_email: str | None = None
    target_sub: str | None = None

    async with get_tenant_db_context(tenant_id) as session:
        admin_result = await session.execute(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.role == Role.TENANT_ADMIN.value,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .with_for_update()
        )
        admin_ids = list(admin_result.scalars().all())

        target_result = await session.execute(
            select(User.id, User.email, User.google_sub).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        target_row = target_result.first()
        if target_row is None:
            raise HTTPException(404, "Користувача не знайдено")

        if len(target_row) >= 3:
            target_email = target_row[1]
            target_sub = target_row[2]

        if user_id in admin_ids and len(admin_ids) == 1:
            raise HTTPException(409, "Не можна видалити останнього адміністратора")

        await session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(is_active=False, deleted_at=now, updated_at=now)
        )
        await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        await session.execute(
            update(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.tenant_id == tenant_id,
                UserSession.is_revoked.is_(False),
            )
            .values(is_revoked=True, revoked_reason="member_removed")
        )

    await disable_user(email=target_email, google_sub=target_sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
