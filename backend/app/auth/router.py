import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text

from app.auth.keycloak_admin import switch_user_tenant_context
from app.auth.permissions import Permission, require_permission, resolve_caller_roles
from app.auth.schemas import SwitchWorkspaceRequest, TokenClaims, WorkspaceItem
from app.db.models.models import Tenant
from app.db.session import AsyncSessionLocal, get_tenant_db_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=TokenClaims)
async def get_me(
    user: TokenClaims = Depends(require_permission(*Permission)),
) -> TokenClaims:
    """Retrieve live DB roles and tenant metadata alongside normalized claims."""
    resolved_roles = await resolve_caller_roles(user)
    tenant_name: str | None = None
    allowed_domain: str | None = None

    try:
        async with get_tenant_db_context(user.tenant_uuid) as session:
            result = await session.execute(
                select(Tenant.name, Tenant.allowed_domain).where(Tenant.id == user.tenant_uuid)
            )
            row = result.first()
            if row is not None:
                tenant_name, allowed_domain = row[0], row[1]
    except Exception as exc:
        logger.warning("Tenant metadata lookup in /auth/me failed for %s: %s", user.tenant_id, exc)

    return user.model_copy(
        update={
            "roles": sorted(resolved_roles),
            "tenant_name": tenant_name,
            "allowed_domain": allowed_domain,
        }
    )


@router.get("/workspaces", response_model=list[WorkspaceItem])
async def list_my_workspaces(
    user: TokenClaims = Depends(require_permission(*Permission)),
) -> list[WorkspaceItem]:
    """List all active workspaces the authenticated user belongs to."""
    workspaces: list[WorkspaceItem] = []

    if user.email:
        try:
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    text(
                        """
                        SELECT tenant_id, tenant_name, tenant_slug, user_role
                        FROM list_workspaces_for_email(:email)
                        """
                    ),
                    {"email": user.email},
                )
                for tid, tname, tslug, role in res.all():
                    workspaces.append(
                        WorkspaceItem(
                            tenant_id=tid,
                            tenant_name=tname,
                            tenant_slug=tslug,
                            role=role,
                            is_current=(tid == user.tenant_uuid),
                        )
                    )
        except Exception as exc:
            logger.warning("Workspace listing lookup failed for %s: %s", user.email, exc)

    if not any(w.tenant_id == user.tenant_uuid for w in workspaces):
        tenant_name = f"Workspace ({user.tenant_id[:8]})"
        tenant_slug = user.tenant_id[:8]
        try:
            async with get_tenant_db_context(user.tenant_uuid) as session:
                row = (
                    await session.execute(
                        select(Tenant.name, Tenant.slug).where(Tenant.id == user.tenant_uuid)
                    )
                ).first()
                if row:
                    tenant_name, tenant_slug = row[0], row[1]
        except Exception:
            pass
        resolved = sorted(await resolve_caller_roles(user))
        workspaces.insert(
            0,
            WorkspaceItem(
                tenant_id=user.tenant_uuid,
                tenant_name=tenant_name,
                tenant_slug=tenant_slug,
                role=resolved[0] if resolved else "DEVELOPER",
                is_current=True,
            ),
        )

    return workspaces


@router.post("/switch-workspace", response_model=WorkspaceItem)
async def switch_workspace(
    body: SwitchWorkspaceRequest,
    user: TokenClaims = Depends(require_permission(*Permission)),
) -> WorkspaceItem:
    """Switch the authenticated user's active tenant attribute and realm role in Keycloak."""
    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email claim is required to switch workspaces.",
        )

    target_row = None
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text(
                """
                SELECT tenant_id, tenant_name, tenant_slug, user_role
                FROM list_workspaces_for_email(:email)
                WHERE tenant_id = :target_tenant_id
                """
            ),
            {"email": user.email, "target_tenant_id": body.tenant_id},
        )
        target_row = res.first()

    if target_row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have active membership in the requested workspace.",
        )

    tid, tname, tslug, target_role = target_row

    await switch_user_tenant_context(
        email=user.email,
        google_sub=user.sub,
        target_tenant_id=str(tid),
        target_role=target_role,
    )

    return WorkspaceItem(
        tenant_id=tid,
        tenant_name=tname,
        tenant_slug=tslug,
        role=target_role,
        is_current=True,
    )
