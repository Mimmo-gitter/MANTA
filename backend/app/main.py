from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import JWKSClient, JWTAuthMiddleware, auth_router
from app.config import settings
from app.tenants import router as tenants_router
from app.webhooks import webhooks_router

app = FastAPI(
    title="Engineering Intelligence Platform",
    version="3.0.0",
)

# Initialize in-memory JWKS client on application state
app.state.jwks_client = JWKSClient(
    jwks_url=settings.jwks_url,
    ttl_seconds=settings.OIDC_JWKS_CACHE_TTL_SECONDS,
    http_timeout=settings.OIDC_JWKS_HTTP_TIMEOUT_SECONDS,
    min_refresh_interval_seconds=settings.OIDC_JWKS_MIN_REFRESH_INTERVAL_SECONDS,
)

# Register JWT authentication middleware FIRST (denies unauthenticated requests on non-public paths)
app.add_middleware(JWTAuthMiddleware)

# Register CORS middleware LAST so it wraps outermost in Starlette's middleware stack.
# This ensures that ALL responses (including preflight OPTIONS and 401/403/500 errors from inner middlewares)
# have CORS headers (Access-Control-Allow-Origin, etc.) attached.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include authentication endpoints
app.include_router(auth_router)

# Include tenant registration endpoints (SCRUM-289)
app.include_router(tenants_router)

# Include webhook intake endpoints (SCRUM-335)
app.include_router(webhooks_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
