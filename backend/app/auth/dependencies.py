from typing import Any

from fastapi import Depends, HTTPException, Request, status

from app.auth.schemas import TokenClaims


def get_current_claims(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency returning the raw dictionary of validated JWT claims
    attached to request.state by JWTAuthMiddleware.
    """
    claims = getattr(request.state, "claims", None)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


def get_current_user(claims: dict[str, Any] = Depends(get_current_claims)) -> TokenClaims:
    """
    FastAPI dependency returning normalized TokenClaims for the authenticated identity.
    """
    return TokenClaims.from_claims(claims)


def get_current_tenant_id(claims: dict[str, Any] = Depends(get_current_claims)) -> str:
    """
    FastAPI dependency returning the validated canonical UUID tenant_id string.
    """
    return claims["tenant_id"]
