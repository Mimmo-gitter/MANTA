from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenClaims

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=TokenClaims)
async def get_me(user: TokenClaims = Depends(get_current_user)) -> TokenClaims:
    """Retrieve normalized token claims of the currently authenticated user."""
    return user
