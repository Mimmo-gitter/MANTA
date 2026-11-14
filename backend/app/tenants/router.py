import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.keycloak_admin import (
    KeycloakError,
    UserAlreadyExistsError,
    create_user,
    delete_user,
)
from app.db.session import get_db, get_tenant_db_context
from app.notifications.router import send_invitation_email
from app.notifications.service import create_invitation
from app.tenants.rate_limiter import registration_rate_limiter
from app.tenants.schemas import TenantRegisterRequest, TenantRegisterResponse

logger = logging.getLogger("app.tenants.router")

router = APIRouter(prefix="/tenants", tags=["Tenants"])


@router.post(
    "/register",
    response_model=TenantRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new organization tenant and first administrator",
)
async def register_tenant(
    req: TenantRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TenantRegisterResponse:
    """
    SCRUM-289: Expose tenant registration through the API using only the least-privilege
    database role (eng_app_user / NOBYPASSRLS).

    Provisions Keycloak user (when password is supplied), atomically invokes `provision_tenant`,
    and creates any initial team invitations submitted from the onboarding wizard.
    """
    registration_rate_limiter.check_rate_limit(request)

    slug = req.get_effective_slug()
    provisional_tenant_id = uuid.uuid4()
    provisional_user_id = uuid.uuid4()
    keycloak_user_id: str | None = None

    if req.password:
        try:
            keycloak_user_id = await create_user(
                email=str(req.admin_email),
                tenant_id=str(provisional_tenant_id),
                full_name=req.admin_name,
                password=req.password,
                role="TENANT_ADMIN",
            )
        except UserAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.message,
            ) from exc
        except KeycloakError as exc:
            logger.error("Keycloak provisioning failed during tenant registration: %s", exc.message)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=exc.message,
            ) from exc

    stmt = text(
        """
        SELECT tenant_id, user_id, slug, admin_email
        FROM provision_tenant(
            p_name => :name,
            p_slug => :slug,
            p_admin_email => :admin_email,
            p_admin_name => :admin_name,
            p_allowed_domain => :allowed_domain,
            p_google_sub => :google_sub,
            p_tenant_id => :tenant_id,
            p_user_id => :user_id,
            p_industry => :industry
        );
        """
    )

    try:
        result = await db.execute(
            stmt,
            {
                "name": req.name,
                "slug": slug,
                "admin_email": str(req.admin_email),
                "admin_name": req.admin_name,
                "allowed_domain": req.allowed_domain,
                "google_sub": keycloak_user_id,
                "tenant_id": provisional_tenant_id,
                "user_id": provisional_user_id,
                "industry": req.industry,
            },
        )
        row = result.first()
        await db.commit()
    except (IntegrityError, DBAPIError) as exc:
        await db.rollback()
        if keycloak_user_id:
            await delete_user(keycloak_user_id)
        err_msg = str(exc).lower()
        if (
            "unique constraint" in err_msg
            or "duplicate key" in err_msg
            or "tenants_slug_key" in err_msg
        ):
            logger.warning(f"Registration conflict for tenant slug: {slug}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An organization with slug '{slug}' already exists.",
            )
        logger.error(f"Failed to provision tenant: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register organization. Please try again later.",
        )

    if not row:
        await db.rollback()
        if keycloak_user_id:
            await delete_user(keycloak_user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provisioning routine returned no record.",
        )

    tenant_id, user_id, res_slug, admin_email = row

    invites_sent = 0
    invite_emails = [
        addr for addr in req.invites if addr.lower() != str(admin_email).lower()
    ]
    if invite_emails:
        created_tokens: list[tuple[str, str]] = []
        try:
            async with get_tenant_db_context(tenant_id) as invite_db:
                for addr in invite_emails:
                    raw_token = await create_invitation(
                        invite_db,
                        tenant_id=tenant_id,
                        invited_by=user_id,
                        email=addr,
                        role="DEVELOPER",
                    )
                    created_tokens.append((addr, raw_token))
            for addr, raw_token in created_tokens:
                try:
                    await send_invitation_email(addr, raw_token)
                    invites_sent += 1
                except Exception as mail_exc:
                    logger.warning(
                        "Invitation created for %s during onboarding but email dispatch failed: %s",
                        addr,
                        mail_exc,
                    )
        except Exception as invite_exc:
            logger.warning(
                "Tenant '%s' registered, but onboarding invitations failed: %s",
                res_slug,
                invite_exc,
            )

    logger.info(
        f"Successfully provisioned tenant '{slug}' ({tenant_id}) with admin '{admin_email}'"
    )

    return TenantRegisterResponse(
        tenant_id=tenant_id,
        admin_id=user_id,
        slug=res_slug,
        name=req.name,
        admin_email=admin_email,
        industry=req.industry,
        invites_sent=invites_sent,
    )
