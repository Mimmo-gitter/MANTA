"""
SCRUM-335, SCRUM-325, SCRUM-326, SCRUM-327, SCRUM-331:
Webhook Intake API Router with Signature Verification, Idempotency, and Failure Resilience.

Features:
- `POST /api/v1/webhooks/{provider}`: Ingests webhooks with automatic provider delivery extraction.
- `POST /api/v1/webhooks/integrations/{integration_id}`: Integration-bound webhook endpoint.
  * Resolves tenant strictly from integration ID (SCRUM-327).
  * Decrypts integration secret and verifies HMAC-SHA256 signature (SCRUM-325, SCRUM-326).
  * Enforces 5-minute replay window on Slack webhooks (SCRUM-326).
- Returns HTTP 202 Accepted immediately upon staging (or idempotent duplicate recognition).
- Returns HTTP 503 with Retry-After header if PostgreSQL is unavailable (SCRUM-331).
"""

from collections.abc import Mapping
import json
import logging
from typing import Any
import uuid

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError, SQLAlchemyError

from app.db.models.models import Integration
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.webhooks.delivery import extract_delivery_id
from app.webhooks.enqueue import enqueue_staged_event_best_effort
from app.webhooks.schemas import WebhookIntakeResponse
from app.webhooks.service import stage_webhook_event_idempotent
from app.webhooks.verification import verify_signature_for_provider

logger = logging.getLogger("app.webhooks.router")

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])

_DB_OUTAGE_EXCEPTIONS = (
    SQLAlchemyError,
    DBAPIError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    TimeoutError,
)

_ROUTE_LOOKUP_SQL = text(
    "SELECT id, tenant_id, provider, webhook_secret_hash, is_active "
    "FROM get_webhook_integration_route(:id)"
)
_SET_TENANT_CONFIG_SQL = text(
    "SELECT set_config('app.current_tenant_id', :tenant_id, true), "
    "set_config('synchronous_commit', 'off', true)"
)
_DECRYPTED_SECRET_CACHE: dict[tuple[str, str], str] = {}


async def _read_request_data(request: Request) -> tuple[dict[str, Any], bytes, str | None]:
    """Safely extracts parsed JSON dict, raw bytes, and validated JSON string from request."""
    raw_body = await request.body()
    payload: dict[str, Any] = {}
    raw_payload_json: str | None = None
    if raw_body:
        try:
            decoded = raw_body.decode("utf-8")
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                payload = parsed
                raw_payload_json = decoded
        except Exception:
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}
    return payload, raw_body, raw_payload_json


@router.post(
    "/{provider}",
    response_model=WebhookIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest incoming webhook with idempotent duplicate delivery protection",
)
async def ingest_webhook_by_provider(
    provider: str,
    request: Request,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
) -> WebhookIntakeResponse:
    """
    SCRUM-335: Ingest webhooks, extract provider-specific delivery identifier,
    and stage into database with ON CONFLICT DO NOTHING.
    SCRUM-331: Returns HTTP 503 with Retry-After if database is unavailable.
    """
    prov = provider.lower().strip()
    if prov not in {"github", "jira", "slack", "argocd", "generic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported webhook provider '{provider}'",
        )

    payload, raw_body, raw_payload_json = await _read_request_data(request)

    # 1. Fail-closed HMAC authentication for ArgoCD; header-triggered for legacy providers
    if prov == "argocd":
        if not x_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Webhook-Secret header for ArgoCD webhook authentication",
                headers={"WWW-Authenticate": "WebhookSignature"},
            )
        verify_signature_for_provider(
            provider=prov,
            secret=x_webhook_secret,
            raw_body=raw_body,
            headers=request.headers,
        )
    elif x_webhook_secret:
        verify_signature_for_provider(
            provider=prov,
            secret=x_webhook_secret,
            raw_body=raw_body,
            headers=request.headers,
        )

    # 2. Resolve tenant ID
    resolved_tenant_id: uuid.UUID
    if x_tenant_id:
        try:
            resolved_tenant_id = uuid.UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-ID format (must be UUID)",
            )
    else:
        # Default fallback tenant for provider-level intake
        resolved_tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # 3. Extract provider delivery ID
    delivery_id = extract_delivery_id(
        provider=prov,
        headers=request.headers,
        payload=payload,
        raw_body=raw_body,
    )

    # 4. Determine event type if present
    event_type = (
        request.headers.get("x-github-event")
        or request.headers.get("x-atlassian-webhook-event")
        or request.headers.get("x-argocd-event")
        or payload.get("hookType")
        or payload.get("event_type")
        or payload.get("type")
        or ("postsync" if prov == "argocd" else "webhook")
    )

    # 5. Idempotently stage event into database under tenant context (SCRUM-331 resilient)
    try:
        async with get_tenant_db_context(resolved_tenant_id) as session:
            result = await stage_webhook_event_idempotent(
                db=session,
                tenant_id=resolved_tenant_id,
                provider=prov,
                provider_delivery_id=delivery_id,
                payload=payload,
                event_type=str(event_type)[:64],
                raw_payload_json=raw_payload_json,
            )
    except _DB_OUTAGE_EXCEPTIONS as exc:
        logger.error(f"Database unavailable during provider webhook staging: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database storage service unavailable. Please retry delivery.",
            headers={"Retry-After": "30"},
        )

    # 6. Immediate post-commit enqueue to Valkey/Redis (SCRUM-388)
    if not result.is_duplicate and result.event_id is not None:
        await enqueue_staged_event_best_effort(
            event_id=result.event_id,
            tenant_id=resolved_tenant_id,
            provider=prov,
            event_type=str(event_type)[:64],
        )

    return WebhookIntakeResponse(
        status="accepted",
        duplicate=result.is_duplicate,
        provider=prov,
        delivery_id=result.delivery_id,
        event_id=result.event_id,
        message=(
            "Duplicate webhook ignored (already staged)"
            if result.is_duplicate
            else "Webhook staged successfully"
        ),
    )


async def _fallback_lookup_integration_route(
    integration_id: uuid.UUID,
    x_tenant_id: str | None,
) -> dict[str, Any] | None:
    """Fallback lookup if SECURITY DEFINER function get_webhook_integration_route is unmigrated."""
    query = select(
        Integration.id,
        Integration.tenant_id,
        Integration.provider,
        Integration.webhook_secret_hash,
        Integration.is_active,
    ).where(
        Integration.id == integration_id,
        Integration.is_active.is_(True),
    )
    if x_tenant_id:
        tenant_uuid = uuid.UUID(x_tenant_id)
        async with get_tenant_db_context(tenant_uuid) as session:
            res = await session.execute(query)
            t_row = res.first()
    else:
        async with AsyncSessionLocal() as session:
            res = await session.execute(query)
            t_row = res.first()

    if not t_row:
        return None
    return {
        "id": t_row[0],
        "tenant_id": t_row[1],
        "provider": t_row[2],
        "webhook_secret_hash": t_row[3],
        "is_active": t_row[4],
    }


def _verify_and_extract_for_integration(
    integration_id: uuid.UUID,
    row: Mapping[str, Any] | None,
    request: Request,
    payload: dict[str, Any],
    raw_body: bytes,
) -> tuple[uuid.UUID, str, str, str]:
    """Validates active integration status, decrypts secret, verifies HMAC, and extracts metadata."""
    if not row or not row.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found or inactive",
        )

    tenant_id: uuid.UUID = row["tenant_id"]
    provider: str = row["provider"]
    secret_hash: str | None = row.get("webhook_secret_hash")

    if provider.lower().strip() == "argocd" and not secret_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing configured webhook secret for ArgoCD integration",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    if secret_hash:
        raw_secret = secret_hash
        if encryption_service.is_encrypted(secret_hash):
            cache_key = (encryption_service.active_key_id, secret_hash)
            cached_secret = _DECRYPTED_SECRET_CACHE.get(cache_key)
            if cached_secret is not None:
                raw_secret = cached_secret
            else:
                try:
                    raw_secret = encryption_service.decrypt(secret_hash)
                    if len(_DECRYPTED_SECRET_CACHE) < 1024:
                        _DECRYPTED_SECRET_CACHE[cache_key] = raw_secret
                except Exception as exc:
                    logger.error(
                        f"Failed to decrypt integration secret for {integration_id}: {exc}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to decrypt integration credential",
                    )

        verify_signature_for_provider(
            provider=provider,
            secret=raw_secret,
            raw_body=raw_body,
            headers=request.headers,
        )

    delivery_id = extract_delivery_id(
        provider=provider,
        headers=request.headers,
        payload=payload,
        raw_body=raw_body,
    )
    event_type = (
        request.headers.get("x-github-event")
        or request.headers.get("x-atlassian-webhook-event")
        or request.headers.get("x-argocd-event")
        or payload.get("hookType")
        or payload.get("event_type")
        or payload.get("type")
        or ("postsync" if provider.lower().strip() == "argocd" else "webhook")
    )
    return tenant_id, provider, delivery_id, str(event_type)[:64]


@router.post(
    "/integrations/{integration_id}",
    response_model=WebhookIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest incoming webhook bound to a specific integration with signature verification",
)
async def ingest_webhook_for_integration(
    integration_id: uuid.UUID,
    request: Request,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
) -> WebhookIntakeResponse:
    """
    SCRUM-327: Resolves tenant strictly from integration ID. Rejects unknown IDs with HTTP 404.
    SCRUM-325 & SCRUM-326: Verifies provider HMAC-SHA256 signature and Slack replay window.
    SCRUM-335: Idempotent staging in webhook_events within a single pooled transaction.
    SCRUM-331: Returns HTTP 503 with Retry-After if database is unavailable.
    """
    payload, raw_body, raw_payload_json = await _read_request_data(request)

    try:
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    res = await session.execute(_ROUTE_LOOKUP_SQL, {"id": integration_id})
                    row = res.mappings().first()

                    tenant_id, provider, delivery_id, event_type = (
                        _verify_and_extract_for_integration(
                            integration_id=integration_id,
                            row=row,
                            request=request,
                            payload=payload,
                            raw_body=raw_body,
                        )
                    )

                    await session.execute(
                        _SET_TENANT_CONFIG_SQL,
                        {"tenant_id": str(tenant_id)},
                    )
                    result = await stage_webhook_event_idempotent(
                        db=session,
                        tenant_id=tenant_id,
                        integration_id=integration_id,
                        provider=provider,
                        provider_delivery_id=delivery_id,
                        payload=payload,
                        event_type=event_type,
                        raw_payload_json=raw_payload_json,
                    )
        except ProgrammingError as p_exc:
            if "get_webhook_integration_route" not in str(p_exc):
                raise
            fallback_row = await _fallback_lookup_integration_route(integration_id, x_tenant_id)
            tenant_id, provider, delivery_id, event_type = _verify_and_extract_for_integration(
                integration_id=integration_id,
                row=fallback_row,
                request=request,
                payload=payload,
                raw_body=raw_body,
            )
            async with get_tenant_db_context(tenant_id) as session:
                result = await stage_webhook_event_idempotent(
                    db=session,
                    tenant_id=tenant_id,
                    integration_id=integration_id,
                    provider=provider,
                    provider_delivery_id=delivery_id,
                    payload=payload,
                    event_type=event_type,
                    raw_payload_json=raw_payload_json,
                )
    except HTTPException:
        raise
    except _DB_OUTAGE_EXCEPTIONS as exc:
        logger.error(
            f"Failed to stage webhook event for integration {integration_id} due to database outage: {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database storage service unavailable. Please retry delivery.",
            headers={"Retry-After": "30"},
        )

    # Immediate post-commit enqueue to Valkey/Redis (SCRUM-388)
    if not result.is_duplicate and result.event_id is not None:
        await enqueue_staged_event_best_effort(
            event_id=result.event_id,
            tenant_id=tenant_id,
            provider=provider,
            event_type=event_type,
        )

    return WebhookIntakeResponse(
        status="accepted",
        duplicate=result.is_duplicate,
        provider=provider,
        delivery_id=result.delivery_id,
        event_id=result.event_id,
        message=(
            "Duplicate webhook ignored (already staged)"
            if result.is_duplicate
            else "Webhook staged successfully"
        ),
    )
