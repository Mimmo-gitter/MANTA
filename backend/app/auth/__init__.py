from app.auth.dependencies import (
    get_current_claims,
    get_current_tenant_id,
    get_current_user,
)
from app.auth.errors import AuthError, JWKSUnavailable, SigningKeyNotFound
from app.auth.jwks import JWKSClient
from app.auth.middleware import JWTAuthMiddleware
from app.auth.permissions import (
    FORBIDDEN_DETAIL,
    FORBIDDEN_RESPONSE,
    Permission,
    PermissionChecker,
    PermissionMatrix,
    Role,
    build_stub_response,
    find_matched_route,
    get_permission_matrix,
    get_route_declared_permissions,
    require_permission,
    resolve_caller_roles,
    stub_route_kwargs,
)
from app.auth.router import router as auth_router
from app.auth.schemas import StubEndpointResponse, TokenClaims

__all__ = [
    "AuthError",
    "JWKSUnavailable",
    "SigningKeyNotFound",
    "JWKSClient",
    "JWTAuthMiddleware",
    "StubEndpointResponse",
    "TokenClaims",
    "Role",
    "Permission",
    "PermissionMatrix",
    "PermissionChecker",
    "find_matched_route",
    "get_permission_matrix",
    "resolve_caller_roles",
    "require_permission",
    "build_stub_response",
    "stub_route_kwargs",
    "get_route_declared_permissions",
    "FORBIDDEN_DETAIL",
    "FORBIDDEN_RESPONSE",
    "get_current_claims",
    "get_current_tenant_id",
    "get_current_user",
    "auth_router",
]
