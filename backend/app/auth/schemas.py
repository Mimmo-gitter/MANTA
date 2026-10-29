from typing import Any
from pydantic import BaseModel


class TokenClaims(BaseModel):
    """Normalized representation of validated JWT claims."""

    sub: str
    email: str | None = None
    preferred_username: str | None = None
    tenant_id: str | None = None
    roles: list[str] = []
    raw: dict[str, Any] = {}

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> "TokenClaims":
        realm_access = claims.get("realm_access")
        roles: list[str] = []
        if isinstance(realm_access, dict):
            roles = realm_access.get("roles") or []
        if not roles:
            direct_roles = claims.get("roles") or claims.get("realmRoles")
            if isinstance(direct_roles, list):
                roles = direct_roles

        return cls(
            sub=claims.get("sub", ""),
            email=claims.get("email"),
            preferred_username=claims.get("preferred_username"),
            tenant_id=claims.get("tenant_id"),
            roles=roles,
            raw=claims,
        )
