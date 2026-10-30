import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
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

    Calls PostgreSQL SECURITY DEFINER function `provision_tenant` atomically.
    """
    # 1. Apply rate limiting per client IP
    registration_rate_limiter.check_rate_limit(request)

    slug = req.get_effective_slug()

    # 2. Invoke SECURITY DEFINER provisioning function under eng_app_user
    stmt = text(
        """
        SELECT tenant_id, user_id, slug, admin_email
        FROM provision_tenant(
            p_name => :name,
            p_slug => :slug,
            p_admin_email => :admin_email,
            p_admin_name => :admin_name,
            p_allowed_domain => :allowed_domain
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
            },
        )
        row = result.first()
        await db.commit()
    except (IntegrityError, DBAPIError) as exc:
        await db.rollback()
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Provisioning routine returned no record.",
        )

    tenant_id, user_id, res_slug, admin_email = row

    logger.info(
        f"Successfully provisioned tenant '{slug}' ({tenant_id}) with admin '{admin_email}'"
    )

    return TenantRegisterResponse(
        tenant_id=tenant_id,
        admin_id=user_id,
        slug=res_slug,
        name=req.name,
        admin_email=admin_email,
    )
