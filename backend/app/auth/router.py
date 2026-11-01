from fastapi import APIRouter, Depends

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import TokenClaims

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=TokenClaims)
async def get_me(
    user: TokenClaims = Depends(require_permission(*Permission)),
) -> TokenClaims:
    """Retrieve normalized token claims of the currently authenticated user."""
    return user
