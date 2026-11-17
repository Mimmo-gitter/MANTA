from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims
from app.contributors.schemas import (
    ContributorErasureResponse,
    ContributorSummaryResponse,
)
from app.contributors.service import (
    erase_contributor_pii,
    get_decrypted_contributor,
)
from app.db.models import Contributor
from app.db.session import get_tenant_db_context

router = APIRouter(prefix="/api/v1/contributors", tags=["Contributors & Privacy"])

require_tenant_admin = require_permission(Permission.MANAGE_TENANT_USERS_AND_ROLES)


@router.get("", response_model=list[ContributorSummaryResponse])
async def list_contributors(
    user: TokenClaims = Depends(require_tenant_admin),
) -> list[ContributorSummaryResponse]:
    """List contributors in the caller's tenant with decrypted PII for active records."""
    async with get_tenant_db_context(user.tenant_uuid) as session:
        res = await session.execute(
            select(Contributor.pseudonym_id)
            .where(Contributor.tenant_id == user.tenant_uuid)
            .order_by(Contributor.created_at.desc())
            .limit(200)
        )
        pseudonyms = list(res.scalars().all())
        items: list[ContributorSummaryResponse] = []
        for pid in pseudonyms:
            data = await get_decrypted_contributor(session, user.tenant_uuid, pid)
            if data is not None:
                items.append(ContributorSummaryResponse(**data))
        return items


@router.get("/{pseudonym_id}", response_model=ContributorSummaryResponse)
async def get_contributor(
    pseudonym_id: str,
    user: TokenClaims = Depends(require_tenant_admin),
) -> ContributorSummaryResponse:
    """Retrieve a single contributor's metadata and decrypted PII under tenant RLS."""
    async with get_tenant_db_context(user.tenant_uuid) as session:
        data = await get_decrypted_contributor(session, user.tenant_uuid, pseudonym_id)
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Contributor '{pseudonym_id}' not found.",
            )
        return ContributorSummaryResponse(**data)


@router.delete("/{pseudonym_id}/erase", response_model=ContributorErasureResponse)
async def erase_contributor(
    pseudonym_id: str,
    user: TokenClaims = Depends(require_tenant_admin),
) -> ContributorErasureResponse:
    """Execute GDPR Article 17 cryptographic erasure for a contributor."""
    async with get_tenant_db_context(user.tenant_uuid) as session:
        erased = await erase_contributor_pii(session, user.tenant_uuid, pseudonym_id)
        if not erased:
            existing = await get_decrypted_contributor(session, user.tenant_uuid, pseudonym_id)
            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Contributor '{pseudonym_id}' not found.",
                )
            return ContributorErasureResponse(
                pseudonym_id=pseudonym_id,
                erased=True,
                detail="Contributor PII was already erased.",
            )
    return ContributorErasureResponse(
        pseudonym_id=pseudonym_id,
        erased=True,
        detail="Contributor PII cryptographically erased.",
    )
