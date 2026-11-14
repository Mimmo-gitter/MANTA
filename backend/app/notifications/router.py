from datetime import datetime, timezone
import inspect
import logging
from email.message import EmailMessage
from uuid import UUID, uuid4

import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keycloak_admin import (
    KeycloakError,
    UserAlreadyExistsError,
    delete_user,
    provision_user,
)
from app.auth.permissions import Permission, Role, require_permission
from app.auth.schemas import TokenClaims
from app.config import settings
from app.db.models.models import Invitation, User
from app.db.session import get_db, get_tenant_db_context
from app.notifications.service import (
    ALLOWED_ROLES,
    INVITATION_TTL_HOURS,
    InvitationError,
    consume_invitation,
    create_invitation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


class InviteRequest(BaseModel):
    email: str
    role: str


class InvitationRoleUpdateRequest(BaseModel):
    role: str


class AcceptRequest(BaseModel):
    token: str


async def send_invitation_email(to_email: str, raw_token: str):
    link = f"{settings.WEB_BASE_URL}/accept-invite?token={raw_token}"
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to_email
    message["Subject"] = "Запрошення до MANTA"
    message.set_content(
        f"Ви запрошені. Посилання діє {INVITATION_TTL_HOURS} год і спрацює один раз:\n{link}"
    )
    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=settings.SMTP_START_TLS,
    )


async def find_platform_user_id(session: AsyncSession, claims: TokenClaims) -> UUID | None:
    """id того, хто запрошує, у таблиці users (для поля invited_by)."""
    conditions = [User.google_sub == claims.sub]
    if claims.email:
        conditions.append(User.email == claims.email)
    result = await session.execute(
        select(User.id).where(User.tenant_id == claims.tenant_uuid, or_(*conditions)).limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/invitations")
async def list_invitations(
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
):
    """List pending (INVITE_SENT) and expired (EXPIRED) invitations for the caller's tenant."""
    now = datetime.now(timezone.utc)
    async with get_tenant_db_context(current_user.tenant_uuid) as session:
        result = await session.execute(
            select(
                Invitation.id,
                Invitation.email,
                Invitation.role,
                Invitation.expires_at,
                Invitation.created_at,
                Invitation.invited_by,
            )
            .where(
                Invitation.tenant_id == current_user.tenant_uuid,
                Invitation.used_at.is_(None),
            )
            .order_by(Invitation.created_at.desc())
        )
        rows = result.all()

    items = []
    for inv_id, inv_email, inv_role, expires_at, created_at, invited_by in rows:
        exp_aware = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        inv_status = "INVITE_SENT" if exp_aware > now else "EXPIRED"
        items.append(
            {
                "id": str(inv_id),
                "email": inv_email,
                "role": str(inv_role),
                "status": inv_status,
                "expires_at": exp_aware.isoformat(),
                "created_at": created_at.isoformat() if created_at else None,
                "invited_by": str(invited_by) if invited_by else None,
            }
        )
    return items


@router.post("/invitations", status_code=201)
async def invite_member(
    body: InviteRequest,
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
):
    # Транзакція комітиться на виході з async with, лист шлемо вже після коміту
    try:
        async with get_tenant_db_context(current_user.tenant_uuid) as session:
            inviter_id = await find_platform_user_id(session, current_user)
            raw_token = await create_invitation(
                session, current_user.tenant_uuid, inviter_id, body.email, body.role
            )
            inv_res = await session.execute(
                select(Invitation.id, Invitation.expires_at)
                .where(
                    Invitation.tenant_id == current_user.tenant_uuid,
                    Invitation.email == body.email.strip().lower(),
                    Invitation.used_at.is_(None),
                )
                .order_by(Invitation.created_at.desc())
                .limit(1)
            )
            raw_first = inv_res.first() if inv_res is not None else None
            if inspect.iscoroutine(raw_first):
                raw_first.close()
                inv_row = None
            else:
                inv_row = raw_first
    except InvitationError as error:
        raise HTTPException(error.status_code, error.message)

    try:
        await send_invitation_email(body.email, raw_token)
    except Exception:
        logger.exception("Не вдалося надіслати лист із запрошенням")  # токен не логуємо
        raise HTTPException(502, "Запрошення створено, але лист надіслати не вдалося")

    invite_url = f"{settings.WEB_BASE_URL}/accept-invite?token={raw_token}"
    response_payload: dict[str, str | None] = {
        "status": "sent",
        "invite_url": invite_url,
    }
    if inv_row is not None and isinstance(inv_row[0], UUID):
        response_payload["id"] = str(inv_row[0])
        response_payload["expires_at"] = (
            inv_row[1].isoformat() if len(inv_row) > 1 and inv_row[1] is not None else None
        )
    return response_payload


@router.patch("/invitations/{invitation_id}/role")
async def update_invitation_role(
    invitation_id: UUID,
    body: InvitationRoleUpdateRequest,
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
):
    """Update the target role of an unused workspace invitation."""
    try:
        new_role = Role(body.role.strip().upper()).value
    except ValueError:
        raise HTTPException(422, "Невідома роль")

    if new_role not in ALLOWED_ROLES:
        raise HTTPException(422, "Невідома роль")

    async with get_tenant_db_context(current_user.tenant_uuid) as session:
        existing = (
            await session.execute(
                select(Invitation.id).where(
                    Invitation.id == invitation_id,
                    Invitation.tenant_id == current_user.tenant_uuid,
                    Invitation.used_at.is_(None),
                )
            )
        ).first()
        if existing is None:
            raise HTTPException(404, "Запрошення не знайдено")

        await session.execute(
            update(Invitation)
            .where(
                Invitation.id == invitation_id,
                Invitation.tenant_id == current_user.tenant_uuid,
            )
            .values(role=new_role)
        )

    return {"id": str(invitation_id), "role": new_role}


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_or_delete_invitation(
    invitation_id: UUID,
    current_user: TokenClaims = Depends(
        require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)
    ),
) -> Response:
    """Revoke a pending invitation or delete an expired invitation."""
    async with get_tenant_db_context(current_user.tenant_uuid) as session:
        existing = (
            await session.execute(
                select(Invitation.id).where(
                    Invitation.id == invitation_id,
                    Invitation.tenant_id == current_user.tenant_uuid,
                    Invitation.used_at.is_(None),
                )
            )
        ).first()
        if existing is None:
            raise HTTPException(404, "Запрошення не знайдено")

        await session.execute(
            delete(Invitation).where(
                Invitation.id == invitation_id,
                Invitation.tenant_id == current_user.tenant_uuid,
            )
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept")
async def accept_invitation(body: AcceptRequest, session: AsyncSession = Depends(get_db)):
    keycloak_user_id = None

    try:
        invitation = await consume_invitation(session, body.token)

        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(invitation["tenant_id"])},
        )

        # 1. Спочатку Keycloak (зовнішню систему транзакцією БД не відкотити)
        keycloak_user_id = await provision_user(
            invitation["email"],
            str(invitation["tenant_id"]),
            role=invitation["role"],
        )

        # 2. Потім наша БД, в одній транзакції разом із used_at
        email_prefix = invitation["email"].split("@")[0]
        session.add(
            User(
                id=uuid4(),
                tenant_id=invitation["tenant_id"],
                email=invitation["email"],
                full_name=email_prefix,
                role=invitation["role"],
                google_sub=keycloak_user_id,
                is_active=True,
            )
        )
        await session.commit()

    except InvitationError as error:
        await session.rollback()
        raise HTTPException(error.status_code, error.message)

    except UserAlreadyExistsError as error:
        await session.rollback()  # used_at відкотиться, посилання лишається дійсним
        raise HTTPException(409, error.message)

    except KeycloakError as error:
        await session.rollback()
        raise HTTPException(502, error.message)

    except IntegrityError as error:
        await session.rollback()
        if keycloak_user_id:
            await delete_user(keycloak_user_id)
        error_text = str(error.orig).lower()
        if "unique" in error_text or "duplicate" in error_text:
            raise HTTPException(409, "Користувач із таким email уже існує")
        logger.exception("Помилка запису користувача в БД")
        raise HTTPException(500, "Не вдалося створити обліковий запис, спробуйте ще раз")

    except Exception:
        await session.rollback()
        if keycloak_user_id:
            await delete_user(keycloak_user_id)
        logger.exception("Помилка прийняття запрошення")
        raise HTTPException(500, "Не вдалося створити обліковий запис, спробуйте ще раз")

    return {"status": "created"}
