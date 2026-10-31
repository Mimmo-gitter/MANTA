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

import json
import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Integration
from app.db.session import get_db, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.webhooks.delivery import extract_delivery_id
from app.webhooks.schemas import WebhookIntakeResponse
from app.webhooks.service import stage_webhook_event_idempotent
from app.webhooks.verification import verify_signature_for_provider

logger = logging.getLogger("app.webhooks.router")

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


async def _read_request_data(request: Request) -> tuple[dict[str, Any], bytes]:
    """Safely extracts parsed JSON dict and raw bytes from incoming request."""
    raw_body = await request.body()
    payload: dict[str, Any] = {}
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {"raw": raw_body.decode("utf-8", errors="replace")}
    return payload, raw_body


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
    if prov not in {"github", "jira", "slack", "generic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported webhook provider '{provider}'",
        )

    payload, raw_body = await _read_request_data(request)

    # 1. Optional signature verification if explicit secret is provided
    if x_webhook_secret:
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
        or payload.get("event_type")
        or payload.get("type")
        or "webhook"
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
            )
    except (SQLAlchemyError, DBAPIError, ConnectionError, OSError) as exc:
        logger.error(f"Database unavailable during provider webhook staging: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database storage service unavailable. Please retry delivery.",
            headers={"Retry-After": "30"},
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
    db: AsyncSession = Depends(get_db),
) -> WebhookIntakeResponse:
    """
    SCRUM-327: Resolves tenant strictly from integration ID. Rejects unknown IDs with HTTP 404.
    SCRUM-325 & SCRUM-326: Verifies provider HMAC-SHA256 signature and Slack replay window.
    SCRUM-335: Idempotent staging in webhook_events.
    SCRUM-331: Returns HTTP 503 with Retry-After if database is unavailable.
    """
    payload, raw_body = await _read_request_data(request)

    # 1. Resolve integration routing metadata (SCRUM-327)
    row = None
    try:
        stmt = text(
            "SELECT id, tenant_id, provider, webhook_secret_hash, is_active "
            "FROM get_webhook_integration_route(:id)"
        )
        res = await db.execute(stmt, {"id": integration_id})
        row = res.mappings().first()
    except (SQLAlchemyError, DBAPIError, ConnectionError, OSError) as exc:
        logger.error(f"Database unavailable during integration route lookup: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database storage service unavailable. Please retry delivery.",
            headers={"Retry-After": "30"},
        )
    except Exception:
        # Fallback query if SECURITY DEFINER function is not yet migrated
        try:
            if x_tenant_id:
                tenant_uuid = uuid.UUID(x_tenant_id)
                async with get_tenant_db_context(tenant_uuid) as session:
                    res = await session.execute(
                        select(
                            Integration.id,
                            Integration.tenant_id,
                            Integration.provider,
                            Integration.webhook_secret_hash,
                            Integration.is_active,
                        ).where(
                            Integration.id == integration_id,
                            Integration.is_active.is_(True),
                        )
                    )
                    t_row = res.first()
            else:
                res = await db.execute(
                    select(
                        Integration.id,
                        Integration.tenant_id,
                        Integration.provider,
                        Integration.webhook_secret_hash,
                        Integration.is_active,
                    ).where(
                        Integration.id == integration_id,
                        Integration.is_active.is_(True),
                    )
                )
                t_row = res.first()

            if t_row:
                row = {
                    "id": t_row[0],
                    "tenant_id": t_row[1],
                    "provider": t_row[2],
                    "webhook_secret_hash": t_row[3],
                    "is_active": t_row[4],
                }
        except (SQLAlchemyError, DBAPIError, ConnectionError, OSError) as exc:
            logger.error(f"Database unavailable during fallback route lookup: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database storage service unavailable. Please retry delivery.",
                headers={"Retry-After": "30"},
            )
        except Exception:
            row = None

    if not row or not row.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found or inactive",
        )

    tenant_id = row["tenant_id"]
    provider = row["provider"]
    secret_hash = row.get("webhook_secret_hash")

    # 2. Decrypt secret and verify HMAC signature (SCRUM-325, SCRUM-326)
    if secret_hash:
        raw_secret = secret_hash
        if encryption_service.is_encrypted(secret_hash):
            try:
                raw_secret = encryption_service.decrypt(secret_hash)
            except Exception as exc:
                logger.error(f"Failed to decrypt integration secret for {integration_id}: {exc}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to decrypt integration credential",
                )

        # Enforce provider-specific signature verification
        verify_signature_for_provider(
            provider=provider,
            secret=raw_secret,
            raw_body=raw_body,
            headers=request.headers,
        )

    # 3. Extract delivery ID
    delivery_id = extract_delivery_id(
        provider=provider,
        headers=request.headers,
        payload=payload,
        raw_body=raw_body,
    )

    # 4. Idempotently stage event under tenant context (SCRUM-331 resilient)
    try:
        async with get_tenant_db_context(tenant_id) as session:
            result = await stage_webhook_event_idempotent(
                db=session,
                tenant_id=tenant_id,
                integration_id=integration_id,
                provider=provider,
                provider_delivery_id=delivery_id,
                payload=payload,
            )
    except (SQLAlchemyError, DBAPIError, ConnectionError, OSError) as exc:
        logger.error(
            f"Failed to stage webhook event for integration {integration_id} due to database outage: {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database storage service unavailable. Please retry delivery.",
            headers={"Retry-After": "30"},
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
