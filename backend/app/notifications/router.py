import logging
from email.message import EmailMessage
from uuid import UUID, uuid4

import aiosmtplib
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keycloak_admin import (
    KeycloakError,
    UserAlreadyExistsError,
    delete_user,
    provision_user,
)
from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.config import settings
from app.db.models.models import User
from app.db.session import get_db, get_tenant_db_context
from app.notifications.service import (
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
    except InvitationError as error:
        raise HTTPException(error.status_code, error.message)

    try:
        await send_invitation_email(body.email, raw_token)
    except Exception:
        logger.exception("Не вдалося надіслати лист із запрошенням")  # токен не логуємо
        raise HTTPException(502, "Запрошення створено, але лист надіслати не вдалося")

    return {"status": "sent"}


@router.post("/invitations/accept")
async def accept_invitation(body: AcceptRequest, session: AsyncSession = Depends(get_db)):
    keycloak_user_id = None

    try:
        invitation = await consume_invitation(session, body.token)

        # set_config(..., true) діє до кінця поточної транзакції
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(invitation["tenant_id"])},
        )

        # 1. Спочатку Keycloak (зовнішню систему транзакцією БД не відкотити)
        keycloak_user_id = await provision_user(invitation["email"], str(invitation["tenant_id"]))

        # 2. Потім наша БД, в одній транзакції разом із used_at
        session.add(
            User(
                id=uuid4(),
                tenant_id=invitation["tenant_id"],
                email=invitation["email"],
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
