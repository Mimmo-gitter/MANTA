"""
SCRUM-394: Contributor Pseudonymization Service.

Features:
- Canonical identity representation '<provider>:<external_actor_id>:<normalized_email>'.
- Deterministic salted HMAC-SHA256 pseudonym generation.
- Per-contributor 32-byte CSPRNG salts encrypted via authenticated AES-256-GCM (SCRUM-321).
- Isolated PII storage: raw names and emails stored encrypted only in the contributors table.
- GDPR Article 17 ("Right to Erasure") cryptographic data nullification and salt zeroization.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
from typing import Any, Dict, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contributor
from app.integrations.crypto import encryption_service

logger = logging.getLogger("app.contributors.service")


def canonicalize_contributor_identity(
    provider: str,
    external_actor_id: str | int,
    raw_email: str | None = None,
) -> str:
    """
    Produces an unambiguous canonical string representation of an external contributor.
    Format: '<clean_provider>:<clean_actor_id>:<clean_email>'
    """
    clean_provider = provider.strip().lower()
    clean_actor_id = str(external_actor_id).strip()
    clean_email = (raw_email or "").strip().lower()
    return f"{clean_provider}:{clean_actor_id}:{clean_email}"


def compute_contributor_pseudonym(salt: bytes, canonical_identity: str) -> str:
    """
    Computes a deterministic 64-character lowercase hex HMAC-SHA256 digest
    using the given salt and canonical identity string.
    """
    return hmac.new(salt, canonical_identity.encode("utf-8"), hashlib.sha256).hexdigest().lower()


async def resolve_or_create_contributor_pseudonym(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    provider: str,
    external_actor_id: str | int,
    raw_email: str | None = None,
    raw_name: str | None = None,
    raw_handle: str | None = None,
) -> str:
    """
    Resolves existing contributor pseudonym or provisions a new one under tenant context.

    Idempotency:
    - Calling twice with the same (tenant_id, provider, external_actor_id) returns identical pseudonym_id.
    - Calling across two different tenants for the same identity produces two completely different pseudonyms.
    """
    clean_provider = provider.strip().lower()
    clean_actor_id = str(external_actor_id).strip()
    canonical = canonicalize_contributor_identity(clean_provider, clean_actor_id, raw_email)

    stmt = select(Contributor).where(
        Contributor.tenant_id == tenant_id,
        Contributor.provider == clean_provider,
        Contributor.external_actor_id == clean_actor_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        if existing.is_erased:
            return existing.pseudonym_id

        # Opportunistic enrichment for newly discovered fields
        needs_flush = False
        if raw_name and not existing.raw_name:
            existing.raw_name = encryption_service.encrypt(raw_name.strip())
            needs_flush = True
        if raw_email and not existing.raw_email:
            existing.raw_email = encryption_service.encrypt(raw_email.strip().lower())
            needs_flush = True
        if raw_handle and not existing.raw_handle:
            existing.raw_handle = raw_handle.strip()
            needs_flush = True

        if needs_flush:
            await db.flush()

        return existing.pseudonym_id

    # Generate isolated 32-byte CSPRNG salt
    salt_bytes = os.urandom(32)
    pseudonym_id = compute_contributor_pseudonym(salt_bytes, canonical)

    # Encrypt salt and raw PII at rest
    encrypted_salt = encryption_service.encrypt(salt_bytes.hex())
    encrypted_name = encryption_service.encrypt(raw_name.strip()) if raw_name else None
    encrypted_email = encryption_service.encrypt(raw_email.strip().lower()) if raw_email else None
    handle = raw_handle.strip() if raw_handle else None

    contributor = Contributor(
        pseudonym_id=pseudonym_id,
        tenant_id=tenant_id,
        provider=clean_provider,
        external_actor_id=clean_actor_id,
        raw_name=encrypted_name,
        raw_email=encrypted_email,
        raw_handle=handle,
        salt_secret=encrypted_salt,
        is_erased=False,
    )
    try:
        async with db.begin_nested():
            db.add(contributor)
            await db.flush()
    except IntegrityError:
        # Concurrent delivery inserted the same (tenant_id, provider, external_actor_id);
        # re-select the committed winner inside the outer transaction.
        existing_res = await db.execute(stmt)
        existing_winner = existing_res.scalar_one_or_none()
        if existing_winner is None:
            raise
        return existing_winner.pseudonym_id

    return pseudonym_id


async def erase_contributor_pii(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pseudonym_id: str,
) -> bool:
    """
    Executes GDPR Article 17 cryptographic erasure for a contributor record.
    Permanently nullifies raw identity columns, destroys the salt secret, and marks is_erased = True.
    """
    stmt = select(Contributor).where(
        Contributor.tenant_id == tenant_id,
        Contributor.pseudonym_id == pseudonym_id,
    )
    result = await db.execute(stmt)
    contributor = result.scalar_one_or_none()

    if contributor is None or contributor.is_erased:
        return False

    contributor.raw_name = None
    contributor.raw_email = None
    contributor.raw_handle = None
    contributor.salt_secret = ""
    contributor.is_erased = True
    contributor.erased_at = datetime.now(timezone.utc)

    await db.flush()
    return True


async def get_decrypted_contributor(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pseudonym_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Retrieves contributor metadata, decrypting raw PII if the contributor is active.
    Returns None if contributor does not exist.
    """
    stmt = select(Contributor).where(
        Contributor.tenant_id == tenant_id,
        Contributor.pseudonym_id == pseudonym_id,
    )
    result = await db.execute(stmt)
    contributor = result.scalar_one_or_none()

    if contributor is None:
        return None

    if contributor.is_erased:
        return {
            "pseudonym_id": contributor.pseudonym_id,
            "tenant_id": contributor.tenant_id,
            "provider": contributor.provider,
            "external_actor_id": contributor.external_actor_id,
            "raw_name": None,
            "raw_email": None,
            "raw_handle": None,
            "is_erased": True,
            "erased_at": contributor.erased_at,
        }

    dec_name = encryption_service.decrypt(contributor.raw_name) if contributor.raw_name else None
    dec_email = encryption_service.decrypt(contributor.raw_email) if contributor.raw_email else None

    return {
        "pseudonym_id": contributor.pseudonym_id,
        "tenant_id": contributor.tenant_id,
        "provider": contributor.provider,
        "external_actor_id": contributor.external_actor_id,
        "raw_name": dec_name,
        "raw_email": dec_email,
        "raw_handle": contributor.raw_handle,
        "is_erased": False,
        "erased_at": None,
    }
