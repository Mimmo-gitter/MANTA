"""
SCRUM-322 & SCRUM-323: Integration Service Layer.

Handles credential encryption, unique webhook URL generation,
and tenant-isolated database persistence.
"""

from datetime import datetime, timezone
import json
import logging
import secrets
from typing import Any, Optional
import uuid

from sqlalchemy import delete, select

from app.config import settings
from app.db.models.models import Integration
from app.db.session import get_tenant_db_context
from app.integrations.crypto import encryption_service, mask_secret
from app.integrations.schemas import (
    IntegrationConnectResponse,
    IntegrationSummaryResponse,
)

logger = logging.getLogger("app.integrations.service")


def generate_webhook_secret() -> str:
    """Generates a cryptographically strong 32-byte (64 hex characters) secret."""
    return secrets.token_hex(32)


def build_webhook_url(base_url: str, integration_id: uuid.UUID) -> str:
    """
    SCRUM-323: Generates the unique, unguessable webhook URL for an integration.
    Guaranteed to route to POST /api/v1/webhooks/integrations/{integration_id}.
    """
    clean_base = (settings.WEBHOOK_BASE_URL or str(base_url)).rstrip("/")
    return f"{clean_base}/api/v1/webhooks/integrations/{integration_id}"


async def save_or_update_integration(
    tenant_id: uuid.UUID,
    provider: str,
    external_account_id: str,
    raw_webhook_secret: str,
    api_credentials: dict[str, Any],
    base_url: str,
) -> IntegrationConnectResponse:
    """
    Encrypts credentials and stores or updates the integration in the database
    under tenant context.
    """
    # 1. Encrypt secrets with AES-256-GCM authenticated encryption (SCRUM-321)
    encrypted_wh_secret = encryption_service.encrypt(raw_webhook_secret)
    encrypted_api_creds = encryption_service.encrypt(json.dumps(api_credentials))

    # 2. Persist inside tenant RLS session
    now = datetime.now(timezone.utc)
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.provider == provider,
            Integration.external_account_id == external_account_id,
        )
        res = await session.execute(stmt)
        integ = res.scalars().first()

        if integ:
            # Reconnection / credential refresh
            integ.webhook_secret_hash = encrypted_wh_secret
            integ.api_credentials_vault_ref = encrypted_api_creds
            integ.is_active = True
            integ.updated_at = now
        else:
            # New integration with cryptographically unguessable UUIDv4 (SCRUM-323)
            integ = Integration(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                provider=provider,
                external_account_id=external_account_id,
                webhook_secret_hash=encrypted_wh_secret,
                api_credentials_vault_ref=encrypted_api_creds,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(integ)

        await session.flush()

        res_id = integ.id
        res_tenant_id = integ.tenant_id
        res_provider = integ.provider
        res_ext_acc = integ.external_account_id
        res_active = integ.is_active
        res_created_at = integ.created_at
        res_updated_at = integ.updated_at

    webhook_url = build_webhook_url(base_url, res_id)

    return IntegrationConnectResponse(
        id=res_id,
        tenant_id=res_tenant_id,
        provider=res_provider,
        external_account_id=res_ext_acc,
        status="connected",
        webhook_url=webhook_url,
        webhook_secret=raw_webhook_secret,
        is_active=res_active,
        created_at=res_created_at,
        updated_at=res_updated_at,
    )


async def list_tenant_integrations(
    tenant_id: uuid.UUID,
    base_url: str,
) -> list[IntegrationSummaryResponse]:
    """Lists all integrations for a given tenant with masked secrets and webhook URLs."""
    async with get_tenant_db_context(tenant_id) as session:
        stmt = (
            select(Integration)
            .where(Integration.tenant_id == tenant_id)
            .order_by(Integration.created_at.desc())
        )
        res = await session.execute(stmt)
        integrations = res.scalars().all()

        summaries: list[IntegrationSummaryResponse] = []
        for integ in integrations:
            masked = "********"
            if integ.webhook_secret_hash:
                try:
                    decrypted = encryption_service.decrypt(integ.webhook_secret_hash)
                    masked = mask_secret(decrypted)
                except Exception:
                    masked = "********"

            summaries.append(
                IntegrationSummaryResponse(
                    id=integ.id,
                    tenant_id=integ.tenant_id,
                    provider=integ.provider,
                    external_account_id=integ.external_account_id,
                    status="connected" if integ.is_active else "disconnected",
                    webhook_url=build_webhook_url(base_url, integ.id),
                    webhook_secret_masked=masked,
                    is_active=integ.is_active,
                    created_at=integ.created_at,
                    updated_at=integ.updated_at,
                )
            )
        return summaries


async def get_tenant_integration(
    tenant_id: uuid.UUID,
    integration_id: uuid.UUID,
    base_url: str,
) -> Optional[IntegrationSummaryResponse]:
    """Retrieves a specific integration by ID for the tenant."""
    async with get_tenant_db_context(tenant_id) as session:
        stmt = select(Integration).where(
            Integration.id == integration_id,
            Integration.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        integ = res.scalars().first()
        if not integ:
            return None

        masked = "********"
        if integ.webhook_secret_hash:
            try:
                decrypted = encryption_service.decrypt(integ.webhook_secret_hash)
                masked = mask_secret(decrypted)
            except Exception:
                masked = "********"

        return IntegrationSummaryResponse(
            id=integ.id,
            tenant_id=integ.tenant_id,
            provider=integ.provider,
            external_account_id=integ.external_account_id,
            status="connected" if integ.is_active else "disconnected",
            webhook_url=build_webhook_url(base_url, integ.id),
            webhook_secret_masked=masked,
            is_active=integ.is_active,
            created_at=integ.created_at,
            updated_at=integ.updated_at,
        )


async def delete_tenant_integration(
    tenant_id: uuid.UUID,
    integration_id: uuid.UUID,
) -> bool:
    """Deletes an integration for the tenant."""
    async with get_tenant_db_context(tenant_id) as session:
        stmt = delete(Integration).where(
            Integration.id == integration_id,
            Integration.tenant_id == tenant_id,
        )
        res = await session.execute(stmt)
        await session.flush()
        return (res.rowcount or 0) > 0
