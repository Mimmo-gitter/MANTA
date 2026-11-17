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
    assert_permission_matrix_complete,
    assert_registered_routes_complete,
    build_stub_response,
    extract_all_app_routes,
    find_matched_route,
    get_permission_matrix,
    get_route_declared_permissions,
    require_permission,
    resolve_caller_roles,
    stub_route_kwargs,
    validate_permission_matrix_completeness,
    validate_registered_routes_completeness,
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
    "extract_all_app_routes",
    "validate_permission_matrix_completeness",
    "assert_permission_matrix_complete",
    "validate_registered_routes_completeness",
    "assert_registered_routes_complete",
    "FORBIDDEN_DETAIL",
    "FORBIDDEN_RESPONSE",
    "get_current_claims",
    "get_current_tenant_id",
    "get_current_user",
    "auth_router",
]
