"""
SCRUM-394 / US-20: Test Suite for Contributor Pseudonymization Service & PII Isolation.

Acceptance Criteria Verified:
- Deterministic, lowercase canonical identity representation.
- Deterministic HMAC-SHA256 pseudonym generation.
- Idempotency: Calling twice with same (tenant_id, canonical_identity) returns identical pseudonym_id.
- Cross-tenant isolation: Same human email across 2 tenants yields 2 completely different pseudonym_ids.
- Encryption at rest: salt_secret and raw PII are encrypted with enc:v1: AES-256-GCM.
- GDPR Art. 17 cryptographic erasure: PII nullified, salt destroyed, pseudonym preserved.
- Tenant RLS isolation: Tenant A cannot view Tenant B's contributors.
"""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.contributors.service import (
    canonicalize_contributor_identity,
    compute_contributor_pseudonym,
    erase_contributor_pii,
    get_decrypted_contributor,
    resolve_or_create_contributor_pseudonym,
)
from app.db.session import async_engine, get_tenant_db_context
from app.main import app


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test."""
    from app.tenants.rate_limiter import registration_rate_limiter

    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


async def register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Helper to register a clean tenant via /tenants/register."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"Contrib Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"admin-{unique}@contribtest.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


def test_canonicalize_contributor_identity():
    """TC-CONTRIB-001: Verifies normalization of casing and whitespace."""
    canon1 = canonicalize_contributor_identity(" GITHUB ", "  12345 ", " Jane.Doe@Example.COM ")
    canon2 = canonicalize_contributor_identity("github", "12345", "jane.doe@example.com")
    assert canon1 == "github:12345:jane.doe@example.com"
    assert canon1 == canon2

    canon_no_email = canonicalize_contributor_identity("github", "999", None)
    assert canon_no_email == "github:999:"


def test_compute_contributor_pseudonym():
    """TC-CONTRIB-002: Verifies deterministic 64-char lowercase hex digest."""
    salt = b"\x01" * 32
    canon = "github:12345:jane.doe@example.com"
    p1 = compute_contributor_pseudonym(salt, canon)
    p2 = compute_contributor_pseudonym(salt, canon)

    assert len(p1) == 64
    assert p1.islower()
    assert p1 == p2

    p3 = compute_contributor_pseudonym(b"\x02" * 32, canon)
    assert p1 != p3


@pytest.mark.asyncio
async def test_resolve_or_create_contributor_pseudonym_idempotent():
    """TC-CONTRIB-003: Calling twice with same identity returns identical pseudonym_id without duplicates."""
    tenant_id, _ = await register_test_tenant("contrib-idemp")

    async with get_tenant_db_context(tenant_id) as session:
        pseudonym_1 = await resolve_or_create_contributor_pseudonym(
            db=session,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id="583231",
            raw_email="octocat@github.com",
            raw_name="Mona Lisa Octocat",
            raw_handle="octocat",
        )
        assert len(pseudonym_1) == 64

        pseudonym_2 = await resolve_or_create_contributor_pseudonym(
            db=session,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id="583231",
            raw_email="octocat@github.com",
            raw_name="Mona Lisa Octocat",
            raw_handle="octocat",
        )
        assert pseudonym_1 == pseudonym_2

        # Verify only 1 row was created in contributors table
        count_res = await session.execute(
            text("SELECT count(*) FROM contributors WHERE tenant_id = :tid;"),
            {"tid": tenant_id},
        )
        assert count_res.scalar_one() == 1


@pytest.mark.asyncio
async def test_resolve_contributor_pseudonym_cross_tenant_isolation():
    """TC-CONTRIB-004: Same identity across two different tenants produces distinct pseudonyms."""
    tenant_a, _ = await register_test_tenant("contrib-iso-a")
    tenant_b, _ = await register_test_tenant("contrib-iso-b")

    shared_email = "lead-dev@corporate.io"
    shared_actor_id = "889900"

    async with get_tenant_db_context(tenant_a) as session_a:
        pseudonym_a = await resolve_or_create_contributor_pseudonym(
            db=session_a,
            tenant_id=tenant_a,
            provider="github",
            external_actor_id=shared_actor_id,
            raw_email=shared_email,
            raw_name="Lead Developer",
        )

    async with get_tenant_db_context(tenant_b) as session_b:
        pseudonym_b = await resolve_or_create_contributor_pseudonym(
            db=session_b,
            tenant_id=tenant_b,
            provider="github",
            external_actor_id=shared_actor_id,
            raw_email=shared_email,
            raw_name="Lead Developer",
        )

    assert pseudonym_a != pseudonym_b, "Cross-tenant pseudonym collision detected"


@pytest.mark.asyncio
async def test_contributor_encryption_at_rest():
    """TC-CONTRIB-005: Raw PII and salt_secret are stored encrypted with enc:v1: prefix."""
    tenant_id, _ = await register_test_tenant("contrib-crypto")

    raw_name = "Super Secret Human"
    raw_email = "confidential@secret.internal"

    async with get_tenant_db_context(tenant_id) as session:
        pseudonym = await resolve_or_create_contributor_pseudonym(
            db=session,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id="101010",
            raw_email=raw_email,
            raw_name=raw_name,
            raw_handle="secretagent",
        )

        # Inspect raw SQL rows directly
        raw_res = await session.execute(
            text(
                "SELECT salt_secret, raw_name, raw_email, raw_handle FROM contributors WHERE pseudonym_id = :pid;"
            ),
            {"pid": pseudonym},
        )
        row = raw_res.fetchone()
        assert row is not None
        salt_secret, enc_name, enc_email, handle = row

        assert salt_secret.startswith("enc:v1:"), "salt_secret is not encrypted"
        assert enc_name.startswith("enc:v1:"), "raw_name is not encrypted"
        assert enc_email.startswith("enc:v1:"), "raw_email is not encrypted"
        assert raw_name not in enc_name, "Plaintext name leaked into encrypted column"
        assert raw_email not in enc_email, "Plaintext email leaked into encrypted column"
        assert handle == "secretagent"

        # Verify decryption helper
        decrypted = await get_decrypted_contributor(session, tenant_id, pseudonym)
        assert decrypted is not None
        assert decrypted["raw_name"] == raw_name
        assert decrypted["raw_email"] == raw_email
        assert decrypted["is_erased"] is False


@pytest.mark.asyncio
async def test_contributor_gdpr_cryptographic_erasure():
    """TC-CONTRIB-006: GDPR Article 17 nullifies PII and zeroes salt while preserving pseudonym."""
    tenant_id, _ = await register_test_tenant("contrib-gdpr")

    async with get_tenant_db_context(tenant_id) as session:
        pseudonym = await resolve_or_create_contributor_pseudonym(
            db=session,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id="777777",
            raw_email="gdpr-requester@privacy.eu",
            raw_name="Privacy Advocate",
            raw_handle="privacy",
        )

        erased = await erase_contributor_pii(session, tenant_id, pseudonym)
        assert erased is True

        # Second erasure call returns False (already erased)
        erased_again = await erase_contributor_pii(session, tenant_id, pseudonym)
        assert erased_again is False

        # Raw DB check
        raw_res = await session.execute(
            text(
                "SELECT salt_secret, raw_name, raw_email, raw_handle, is_erased, erased_at "
                "FROM contributors WHERE pseudonym_id = :pid;"
            ),
            {"pid": pseudonym},
        )
        row = raw_res.fetchone()
        assert row is not None
        salt_secret, enc_name, enc_email, handle, is_erased, erased_at = row

        assert salt_secret == "", "Salt was not zeroized"
        assert enc_name is None, "Name was not nullified"
        assert enc_email is None, "Email was not nullified"
        assert handle is None, "Handle was not nullified"
        assert is_erased is True
        assert erased_at is not None

        # Verify decrypted view returns None PII
        decrypted = await get_decrypted_contributor(session, tenant_id, pseudonym)
        assert decrypted is not None
        assert decrypted["raw_name"] is None
        assert decrypted["raw_email"] is None
        assert decrypted["is_erased"] is True


@pytest.mark.asyncio
async def test_contributor_rls_tenant_isolation():
    """TC-CONTRIB-007: Under tenant A session, tenant B's contributors are invisible."""
    tenant_a, _ = await register_test_tenant("contrib-rls-a")
    tenant_b, _ = await register_test_tenant("contrib-rls-b")

    async with get_tenant_db_context(tenant_a) as session_a:
        pseudo_a = await resolve_or_create_contributor_pseudonym(
            db=session_a,
            tenant_id=tenant_a,
            provider="github",
            external_actor_id="111",
            raw_email="dev-a@tenant-a.com",
        )

    async with get_tenant_db_context(tenant_b) as session_b:
        pseudo_b = await resolve_or_create_contributor_pseudonym(
            db=session_b,
            tenant_id=tenant_b,
            provider="github",
            external_actor_id="222",
            raw_email="dev-b@tenant-b.com",
        )

        # Tenant B cannot select Tenant A's row
        check_a = await session_b.execute(
            text("SELECT count(*) FROM contributors WHERE pseudonym_id = :pid;"),
            {"pid": pseudo_a},
        )
        assert check_a.scalar_one() == 0

        # Tenant B can only see their own row
        check_b = await session_b.execute(
            text("SELECT count(*) FROM contributors WHERE pseudonym_id = :pid;"),
            {"pid": pseudo_b},
        )
        assert check_b.scalar_one() == 1
