from datetime import datetime, timezone
import ipaddress
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client_factory import (
    AI_MODE_LOCAL_ONLY,
    LocalOnlyPolicyViolationError,
    assert_local_only_endpoint,
    get_mode_metadata,
    normalize_ai_processing_mode,
)
from app.ai.reindex_service import (
    TenantReindexState,
    enqueue_adr_reembed_job,
    get_tenant_reindex_status,
)
from app.auth.keycloak_admin import (
    KeycloakError,
    UserAlreadyExistsError,
    create_user,
    delete_user,
)
from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.config import settings
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import AuditLog, Tenant, User
from app.db.session import get_db, get_tenant_db_context
from app.notifications.router import send_invitation_email
from app.notifications.service import create_invitation
from app.tenants.rate_limiter import registration_rate_limiter
from app.tenants.schemas import (
    TenantRegisterRequest,
    TenantRegisterResponse,
    TenantSettingsResponse,
    TenantSettingsUpdateRequest,
)

logger = logging.getLogger("app.tenants.router")

router = APIRouter(prefix="/tenants", tags=["Tenants"])
settings_router = APIRouter(prefix="/api/v1/tenants", tags=["Tenants"])
require_tenant_admin = require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)


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
    invite_emails = [addr for addr in req.invites if addr.lower() != str(admin_email).lower()]
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


def _settings_snapshot(tenant: Tenant) -> dict[str, object]:
    return {
        "name": tenant.name,
        "allowed_domain": tenant.allowed_domain,
        "industry": tenant.industry,
        "ai_processing_mode": normalize_ai_processing_mode(tenant.ai_processing_mode),
        "retention_days": tenant.retention_days,
    }


def _safe_client_ip(request: Request) -> str | None:
    raw_host = request.client.host if request.client else None
    if not raw_host:
        return None
    try:
        return str(ipaddress.ip_address(raw_host))
    except ValueError:
        return None


async def _resolve_actor_user_id(db: AsyncSession, current_user: TokenClaims) -> uuid.UUID | None:
    predicates = []
    if current_user.sub:
        predicates.append(User.google_sub == current_user.sub)
    if current_user.email:
        predicates.append(func.lower(User.email) == current_user.email.strip().lower())
    if current_user.user_id is not None:
        predicates.append(User.id == current_user.user_id)
    if not predicates:
        return None
    return await db.scalar(
        select(User.id).where(User.tenant_id == current_user.tenant_uuid, or_(*predicates)).limit(1)
    )


def _build_tenant_settings_response(
    tenant: Tenant, reindex_state: TenantReindexState
) -> TenantSettingsResponse:
    meta = get_mode_metadata(tenant.ai_processing_mode)
    return TenantSettingsResponse(
        tenant_id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        allowed_domain=tenant.allowed_domain,
        industry=tenant.industry,
        tier=tenant.tier,
        status=tenant.status,
        max_projects=tenant.max_projects,
        ai_processing_mode=meta["mode"],
        retention_days=tenant.retention_days,
        active_embedding_dimensions=int(meta["embedding_dimensions"]),
        active_embedding_model=str(meta["embedding_model"]),
        active_llm_model=str(meta["llm_model"]),
        reindexing=reindex_state.reindexing,
        reindex_status=reindex_state.status,
        reindexed_count=reindex_state.reindexed_count,
        reindex_total_count=reindex_state.total_count,
        reindex_job_id=reindex_state.job_id,
        reindex_error=reindex_state.error,
        updated_at=tenant.updated_at,
    )


@settings_router.get(
    "/settings",
    response_model=TenantSettingsResponse,
    summary="Retrieve workspace settings, AI compliance mode, and live ADR re-indexing state",
)
async def get_tenant_settings(
    request: Request,
    current_user: TokenClaims = Depends(require_tenant_admin),
) -> TenantSettingsResponse:
    async with get_tenant_db_context(current_user.tenant_uuid) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == current_user.tenant_uuid))
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    redis_override = getattr(request.app.state, "ai_reindex_redis", None)
    reindex_state = await get_tenant_reindex_status(
        current_user.tenant_uuid, redis_client=redis_override
    )
    return _build_tenant_settings_response(tenant, reindex_state)


@settings_router.patch(
    "/settings",
    response_model=TenantSettingsResponse,
    summary="Update workspace settings, AI processing mode, and retention policy",
)
async def update_tenant_settings(
    req: TenantSettingsUpdateRequest,
    request: Request,
    current_user: TokenClaims = Depends(require_tenant_admin),
) -> TenantSettingsResponse:
    redis_override = getattr(request.app.state, "ai_reindex_redis", None)
    mode_changed_to: str | None = None

    async with get_tenant_db_context(current_user.tenant_uuid) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == current_user.tenant_uuid))
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        payload_before = _settings_snapshot(tenant)
        previous_mode = str(payload_before["ai_processing_mode"])

        fields_set = req.model_fields_set
        mutated = False

        if "name" in fields_set and req.name is not None and req.name != tenant.name:
            tenant.name = req.name
            mutated = True

        if "allowed_domain" in fields_set and req.allowed_domain != tenant.allowed_domain:
            tenant.allowed_domain = req.allowed_domain
            mutated = True

        if "industry" in fields_set and req.industry != tenant.industry:
            tenant.industry = req.industry
            mutated = True

        if "retention_days" in fields_set and req.retention_days is not None:
            if req.retention_days != tenant.retention_days:
                tenant.retention_days = req.retention_days
                mutated = True

        if "ai_processing_mode" in fields_set and req.ai_processing_mode is not None:
            normalized_new = normalize_ai_processing_mode(req.ai_processing_mode)
            if normalized_new == AI_MODE_LOCAL_ONLY:
                try:
                    assert_local_only_endpoint(settings.LOCAL_LLM_BASE_URL)
                except LocalOnlyPolicyViolationError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=str(exc),
                    ) from exc

            if normalized_new != previous_mode or tenant.ai_processing_mode != normalized_new:
                tenant.ai_processing_mode = normalized_new
                mutated = True
                if normalized_new != previous_mode:
                    mode_changed_to = normalized_new

        if mutated:
            tenant.updated_at = datetime.now(timezone.utc)
            actor_id = await _resolve_actor_user_id(db, current_user)
            db.add(
                AuditLog(
                    id=generate_uuidv7(),
                    tenant_id=tenant.id,
                    actor_id=actor_id,
                    action="tenant.settings.updated",
                    entity_type="tenant",
                    entity_id=str(tenant.id),
                    ip_address=_safe_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    payload_before=payload_before,
                    payload_after=_settings_snapshot(tenant),
                )
            )
            await db.flush()

    if mode_changed_to is not None:
        reindex_state = await enqueue_adr_reembed_job(
            tenant_id=current_user.tenant_uuid,
            target_mode=mode_changed_to,
            priority="high",
            redis_client=redis_override,
        )
    else:
        reindex_state = await get_tenant_reindex_status(
            current_user.tenant_uuid, redis_client=redis_override
        )

    return _build_tenant_settings_response(tenant, reindex_state)
