from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenClaims
from app.db.session import get_tenant_db
from .schemas import RegisterMobileDeviceRequest, RegisterMobileDeviceResponse
from .service import resolve_or_create_mobile_device, delete_mobile_device

mobile_router = APIRouter(prefix="/api/v1/mobile", tags=["Mobile & FCM"])

@mobile_router.post(
    "/devices",
    status_code=status.HTTP_200_OK,
    summary="Register or refresh an FCM device",
    response_model=RegisterMobileDeviceResponse,
    description=(
        "Registers the authenticated user's FCM token for the current tenant, or refreshes "
        "the existing registration's metadata and last-seen time. Repeating the request "
        "reactivates an inactive registration. Tenant and user identity are derived from "
        "the authenticated session."
    ),
)
async def register_mobile_device(
    payload: RegisterMobileDeviceRequest,
    user: TokenClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> RegisterMobileDeviceResponse:
    """Registers a new mobile client."""
    if user.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID claim is required to register a new mobile device.",
        )

    return await resolve_or_create_mobile_device(db,
        user.tenant_uuid,
        user.user_id,
        payload,
    )


@mobile_router.delete(
    "/devices/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an FCM device registration",
    description=(
        "Permanently deletes the authenticated user's device registration matching this FCM "
        "token. The operation is scoped to the current tenant and user, and returns 204 "
        "whether or not a matching registration exists."
    ),
)
async def remove_mobile_device(
    token: str,
    user: TokenClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Removes existing mobile client by its FCM token."""
    if user.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User ID claim is required to register a new mobile device.",
        )
    if len(token.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="FCM token must not be blank.",
        )

    await delete_mobile_device(db, user.tenant_uuid, user.user_id, token)
