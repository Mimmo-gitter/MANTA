import logging

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.errors import AuthError, JWKSUnavailable
from app.auth.jwks import JWKSClient
from app.auth.tokens import decode_token, extract_bearer_token, read_kid
from app.config import settings

logger = logging.getLogger("app.auth.middleware")

PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/tenants/register",
    }
)
PUBLIC_PATH_PREFIXES: tuple[str, ...] = ()

UNAUTHORIZED = {"detail": "Not authenticated"}


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    FastAPI / Starlette middleware enforcing RS256 JWT authentication across
    all protected HTTP routes.

    Note: Starlette BaseHTTPMiddleware only intercepts HTTP requests; WebSocket
    scopes pass through untouched (governed by dedicated WebSocket handlers).
    """

    @staticmethod
    def is_public(path: str) -> bool:
        if path in PUBLIC_PATHS:
            return True
        for prefix in PUBLIC_PATH_PREFIXES:
            if path.startswith(prefix):
                return True
        return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Exempt CORS preflight and public health/documentation endpoints
        if request.method == "OPTIONS" or self.is_public(request.url.path):
            return await call_next(request)

        client: JWKSClient | None = getattr(request.app.state, "jwks_client", None)
        if client is None:
            logger.error("JWKSClient not configured on app.state")
            return JSONResponse(
                {"detail": "Authentication service misconfigured"},
                status_code=503,
            )

        try:
            token = extract_bearer_token(request.headers.get("Authorization"))
            kid = read_kid(token)
            key = await client.get_signing_key(kid)
            claims = decode_token(
                token,
                key,
                audience=settings.OIDC_AUDIENCE,
                issuer=settings.allowed_issuers,
            )
            request.state.claims = claims
            request.state.tenant_id = claims["tenant_id"]
        except JWKSUnavailable:
            logger.warning("JWKS unavailable during token verification")
            return JSONResponse(
                {"detail": "Authentication service unavailable"},
                status_code=503,
            )
        except (AuthError, jwt.PyJWTError) as exc:
            logger.debug("Token validation rejected: %s", exc)
            return JSONResponse(
                UNAUTHORIZED,
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
