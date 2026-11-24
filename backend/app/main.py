from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.auth.members_router import router as members_router
from app.notifications.router import router as invitations_router

from app.adr import adr_router
from app.auth import JWKSClient, JWTAuthMiddleware, auth_router
from app.config import settings
from app.contributors.router import router as contributors_router
from app.dead_letters import dead_letters_router
from app.deployments import deployments_router
from app.dora import dora_router
from app.integrations import integrations_router
from app.observability import (
    close_metrics_redis,
    refresh_broker_metrics,
    setup_prometheus_instrumentation,
)
from app.projects import projects_router
from app.refactoring import refactoring_router
from app.search import search_router
from app.tenants import router as tenants_router
from app.webhooks import webhooks_router
from app.mobile import mobile_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.assert_production_security()
    try:
        yield
    finally:
        await close_metrics_redis()


app = FastAPI(
    title="Engineering Intelligence Platform",
    version="3.0.0",
    lifespan=lifespan,
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

# Instrument HTTP requests & webhook ACK latency before CORS so CORS stays outermost (TASK-302-1)
setup_prometheus_instrumentation(app)

# Register CORS middleware LAST so it wraps outermost in Starlette's middleware stack.
# This ensures that ALL responses (including preflight OPTIONS and 401/403/500 errors from inner middlewares)
# have CORS headers (Access-Control-Allow-Origin, etc.) attached.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include authentication endpoints
app.include_router(auth_router)

# Include tenant registration endpoints (SCRUM-289)
app.include_router(tenants_router)

# Include contributor pseudonym & GDPR erasure endpoints
app.include_router(contributors_router)

# Include webhook intake endpoints (SCRUM-335)
app.include_router(webhooks_router)

# Include integration connection and management endpoints (SCRUM-322, SCRUM-323)
app.include_router(integrations_router)
app.include_router(dead_letters_router)

# Member invitations (SCRUM-314, SCRUM-315)
app.include_router(invitations_router)

# Member role management (SCRUM-317)
app.include_router(members_router)
# Include stub endpoints for future iteration actions (SCRUM-308)
app.include_router(dora_router)
app.include_router(projects_router)
app.include_router(deployments_router)
app.include_router(refactoring_router)
app.include_router(search_router)
app.include_router(adr_router)

# Mobile FCM token registration endpoints (SCRUM-494)
app.include_router(mobile_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    override_redis = getattr(request.app.state, "metrics_redis", None)
    await refresh_broker_metrics(redis_client=override_redis)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
