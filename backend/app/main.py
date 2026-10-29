from fastapi import FastAPI

from app.auth import JWKSClient, JWTAuthMiddleware, auth_router
from app.config import settings

app = FastAPI(
    title="Engineering Intelligence Platform",
    version="3.0.0",
)

# Initialize in-memory JWKS client on application state
app.state.jwks_client = JWKSClient(
    jwks_url=settings.jwks_url,
    ttl_seconds=settings.OIDC_JWKS_CACHE_TTL_SECONDS,
    http_timeout=settings.OIDC_JWKS_HTTP_TIMEOUT_SECONDS,
)

# Register JWT authentication middleware (denies unauthenticated requests on non-public paths)
app.add_middleware(JWTAuthMiddleware)

# Include authentication endpoints
app.include_router(auth_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
