"""
SCRUM-335: Webhook Intake API Router with Idempotent Delivery Staging.

Exposes:
- `POST /api/v1/webhooks/{provider}`: Ingests webhooks with automatic provider delivery extraction.
- `POST /api/v1/webhooks/integrations/{integration_id}`: Integration-bound webhook endpoint.

All endpoints return HTTP 202 Accepted immediately upon staging (or idempotent duplicate recognition).
"""

import json
import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Integration
from app.db.session import get_db, get_tenant_db_context
from app.webhooks.delivery import extract_delivery_id
from app.webhooks.schemas import WebhookIntakeResponse
from app.webhooks.service import stage_webhook_event_idempotent

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
    db: AsyncSession = Depends(get_db),
) -> WebhookIntakeResponse:
    """
    SCRUM-335: Ingest webhooks, extract provider-specific delivery identifier,
    and stage into database with ON CONFLICT DO NOTHING.
    """
    prov = provider.lower().strip()
    if prov not in {"github", "jira", "slack", "generic"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported webhook provider '{provider}'",
        )

    payload, raw_body = await _read_request_data(request)

    # 1. Resolve tenant ID (from header, or payload metadata if allowed for dev)
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
        # Default or fallback tenant for decoupled intake tests
        # (In production with integration_id, tenant is resolved strictly from DB)
        resolved_tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    # 2. Extract provider delivery ID
    delivery_id = extract_delivery_id(
        provider=prov,
        headers=request.headers,
        payload=payload,
        raw_body=raw_body,
    )

    # 3. Determine event type if present
    event_type = (
        request.headers.get("x-github-event")
        or request.headers.get("x-atlassian-webhook-event")
        or payload.get("event_type")
        or payload.get("type")
        or "webhook"
    )

    # 4. Idempotently stage event into database under tenant context
    async with get_tenant_db_context(resolved_tenant_id) as session:
        result = await stage_webhook_event_idempotent(
            db=session,
            tenant_id=resolved_tenant_id,
            provider=prov,
            provider_delivery_id=delivery_id,
            payload=payload,
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


@router.post(
    "/integrations/{integration_id}",
    response_model=WebhookIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest incoming webhook bound to a specific integration",
)
async def ingest_webhook_for_integration(
    integration_id: uuid.UUID,
    request: Request,
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db),
) -> WebhookIntakeResponse:
    """
    Ingest webhook for a specific integration.
    Resolves tenant_id directly from the integration record in the database.
    """
    payload, raw_body = await _read_request_data(request)

    # Resolve integration from DB (under tenant context if provided, or default session)
    if x_tenant_id:
        try:
            tenant_uuid = uuid.UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-ID format (must be UUID)",
            )
        async with get_tenant_db_context(tenant_uuid) as session:
            res = await session.execute(
                select(Integration.id, Integration.tenant_id, Integration.provider).where(
                    Integration.id == integration_id, Integration.is_active.is_(True)
                )
            )
            row = res.first()
    else:
        res = await db.execute(
            select(Integration.id, Integration.tenant_id, Integration.provider).where(
                Integration.id == integration_id, Integration.is_active.is_(True)
            )
        )
        row = res.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found or inactive",
        )

    _, tenant_id, provider = row

    # Extract delivery ID
    delivery_id = extract_delivery_id(
        provider=provider,
        headers=request.headers,
        payload=payload,
        raw_body=raw_body,
    )

    # Idempotently stage event under tenant context
    async with get_tenant_db_context(tenant_id) as session:
        result = await stage_webhook_event_idempotent(
            db=session,
            tenant_id=tenant_id,
            integration_id=integration_id,
            provider=provider,
            provider_delivery_id=delivery_id,
            payload=payload,
        )

    return WebhookIntakeResponse(
        status="accepted",
        duplicate=result.is_duplicate,
        provider=provider,
        delivery_id=result.delivery_id,
        event_id=result.event_id,
    )
