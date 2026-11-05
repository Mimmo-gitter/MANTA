from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update

from app.auth.permissions import Permission, Role, require_permission
from app.auth.schemas import TokenClaims
from app.db.models.models import User, UserRole
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
        result = await session.execute(
            select(User.id, User.email, User.role)
            .where(User.tenant_id == current_user.tenant_uuid, User.is_active.is_(True))
            .order_by(User.email)
        )
        rows = result.all()

    members = []
    for member_id, member_email, member_role in rows:
        members.append({"id": str(member_id), "email": member_email, "role": str(member_role)})
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

    # HTTPException усередині async with відкочує транзакцію
    async with get_tenant_db_context(tenant_id) as session:
        # Блокуємо рядки адмінів: два одночасні запити не знизять останнього
        admin_result = await session.execute(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.role == Role.TENANT_ADMIN.value,
                User.is_active.is_(True),
            )
            .with_for_update()
        )
        admin_ids = []
        for admin_id in admin_result.scalars().all():
            admin_ids.append(admin_id)

        target_result = await session.execute(
            select(User.id).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
            )
        )
        if target_result.first() is None:
            raise HTTPException(404, "Користувача не знайдено")

        target_is_admin = user_id in admin_ids
        is_demotion = target_is_admin and new_role != Role.TENANT_ADMIN.value
        if is_demotion and len(admin_ids) == 1:
            raise HTTPException(409, "Не можна понизити останнього адміністратора")

        await session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(role=new_role)
        )
        # Інакше старі ролі з user_roles залишаться в resolve_caller_roles
        await session.execute(delete(UserRole).where(UserRole.user_id == user_id))

    return {"user_id": str(user_id), "role": new_role}
