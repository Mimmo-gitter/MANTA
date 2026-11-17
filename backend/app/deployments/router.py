"""FastAPI router for direct deployment ingestion (Task 25.4)."""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.db.session import get_tenant_db
from app.deployments.schemas import DirectDeploymentIngestRequest, DirectDeploymentResponse
from app.deployments.service import ingest_direct_deployment

router = APIRouter(prefix="/api/v1/projects", tags=["Projects & Deployments"])


@router.post(
    "/{project_id}/deployments",
    response_model=DirectDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest Direct Deployment Outcome",
    description=(
        "Record a production or non-production deployment outcome directly from a CI/CD "
        "pipeline under JWT authentication, INGEST_PROJECT_DEPLOYMENTS RBAC, and tenant RLS. "
        "Deployer identities are pseudonymized on intake via Task 20.3."
    ),
)
async def create_project_deployment(
    project_id: uuid.UUID,
    payload: DirectDeploymentIngestRequest,
    response: Response,
    user: TokenClaims = Depends(require_permission(Permission.INGEST_PROJECT_DEPLOYMENTS)),
    db: AsyncSession = Depends(get_tenant_db),
) -> DirectDeploymentResponse:
    """Persist a direct deployment event with normalized environment and pseudonymized actor."""
    result = await ingest_direct_deployment(
        db=db,
        tenant_id=user.tenant_uuid,
        project_id=project_id,
        payload=payload,
        user=user,
    )
    if result.deduplicated:
        response.status_code = status.HTTP_200_OK
    return result
