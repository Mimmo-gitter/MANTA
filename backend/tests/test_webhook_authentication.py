"""
SCRUM-325, SCRUM-326, SCRUM-327, SCRUM-328, SCRUM-331, SCRUM-351:
Comprehensive Automated Pytest Suite for Webhook Authentication, Signature Verification,
Replay Protection, Tenant Routing, and Database Outage Resilience.
Documented test scenarios: docs/webhook-auth-test-scenarios.md

Parent User Story: SCRUM-250 (US-14: Authenticated webhook intake)

Scope & Verification Coverage:
1. Provider Payload Signing Helpers (SCRUM-328 Scope 1):
   - `sign_github_payload`: Signs raw payload bytes like GitHub (`X-Hub-Signature-256: sha256=<hex>`
     or legacy `X-Hub-Signature`).
   - `sign_jira_payload`: Signs raw payload bytes like Jira (`X-Hub-Signature-256: sha256=<hex>`
     or `X-Atlassian-Webhook-Signature`).
   - `sign_slack_payload`: Signs `v0:{timestamp}:{raw_body}` like Slack (`X-Slack-Signature: v0=<hex>`
     + `X-Slack-Request-Timestamp`).
   - `sign_generic_payload` & `sign_payload_for_provider`: Unified provider signing helpers.
2. Per-Scenario Automated Tests (SCRUM-328 Scope 2):
   - GitHub HMAC-SHA256 valid signature acceptance, raw byte formatting preservation, tampered payload
     rejection (HTTP 401), wrong secret rejection (HTTP 401), missing/empty/malformed header rejection (HTTP 401),
     and zero-row database persistence on failure.
   - Jira HMAC-SHA256 valid signature acceptance across `X-Hub-Signature-256` and
     `X-Atlassian-Webhook-Signature`, tampered body rejection (HTTP 401), wrong secret rejection (HTTP 401),
     missing header rejection (HTTP 401), and zero-row persistence on failure.
   - Slack `v0` HMAC-SHA256 valid signature acceptance, strict 5-minute (300s) replay window boundary
     enforcement (past & future timestamps > 300s rejected with HTTP 401), timestamp-swap replay attack
     rejection (HTTP 401), missing/malformed timestamp or signature header rejection (HTTP 401), and
     zero-row persistence on failure.
   - Provider-level endpoint (`/api/v1/webhooks/{provider}` with `X-Webhook-Secret`) and integration-bound
     endpoint (`/api/v1/webhooks/integrations/{integration_id}` with AES-256-GCM encrypted secrets).
   - Tenant resolution strictly from integration ID; spoofed `X-Tenant-ID` ignored; unknown and inactive
     integration IDs rejected with HTTP 404 (SCRUM-327).
   - Constant-time `hmac.compare_digest` enforcement across all provider verifiers.
   - PostgreSQL outage resilience returning HTTP 503 with `Retry-After: 30` (SCRUM-331).
3. Acceptance Criteria Guardrail (SCRUM-328 AC 2):
   - Explicitly verifies that removing signature verification makes the authentication and replay
     scenario tests fail.
"""

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
import app.webhooks.router as webhook_router
import app.webhooks.verification as webhook_verification
from app.webhooks.verification import (
    SLACK_MAX_REPLAY_AGE_SECONDS,
    verify_github_signature,
    verify_jira_signature,
    verify_signature_for_provider,
    verify_slack_signature,
)


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset rate limiters per test event loop."""
    registration_rate_limiter.reset()
    webhook_router._DECRYPTED_SECRET_CACHE.clear()
    yield
    registration_rate_limiter.reset()
    webhook_router._DECRYPTED_SECRET_CACHE.clear()
    await async_engine.dispose()


# ============================================================================
# 1. Helper Functions That Sign Payloads Like Each Provider (SCRUM-328 Scope 1)
# ============================================================================


def _serialize_payload_bytes(payload: bytes | str | dict[str, Any]) -> bytes:
    """Normalizes a dict, string, or bytes payload into raw request bytes."""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload).encode("utf-8")


def _compute_github_signature(secret: str, body: bytes, *, prefix: bool = True) -> str:
    """Computes GitHub HMAC-SHA256 digest (`sha256=<hex>` or raw `<hex>`)."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}" if prefix else mac


def _compute_jira_signature(secret: str, body: bytes, *, prefix: bool = True) -> str:
    """Computes Jira HMAC-SHA256 digest (`sha256=<hex>` or raw `<hex>`)."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}" if prefix else mac


def _compute_slack_signature(
    secret: str,
    timestamp: str | int | float,
    body: bytes,
    *,
    version: str = "v0",
) -> str:
    """Computes Slack HMAC-SHA256 signature over `{version}:{timestamp}:{body}`."""
    base = f"{version}:{timestamp}:{body.decode('utf-8', errors='replace')}"
    mac = hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{version}={mac}"


def _compute_generic_signature(secret: str, body: bytes, *, prefix: bool = True) -> str:
    """Computes generic webhook HMAC-SHA256 signature."""
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}" if prefix else mac


def sign_github_payload(
    secret: str,
    payload: bytes | str | dict[str, Any],
    *,
    delivery_id: str | None = None,
    event_type: str = "push",
    header_name: str = "X-Hub-Signature-256",
    prefix: bool = True,
) -> tuple[bytes, dict[str, str]]:
    """
    Signs a webhook payload like GitHub (`X-Hub-Signature-256: sha256=<hex>`).
    Returns `(raw_body_bytes, headers_dict)`.
    """
    raw_body = _serialize_payload_bytes(payload)
    signature = _compute_github_signature(secret, raw_body, prefix=prefix)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": delivery_id or str(uuid.uuid4()),
        "X-GitHub-Event": event_type,
        header_name: signature,
    }
    return raw_body, headers


def sign_jira_payload(
    secret: str,
    payload: bytes | str | dict[str, Any],
    *,
    delivery_id: str | None = None,
    webhook_event: str = "jira:issue_updated",
    header_name: str = "X-Hub-Signature-256",
    prefix: bool = True,
) -> tuple[bytes, dict[str, str]]:
    """
    Signs a webhook payload like Jira (`X-Hub-Signature-256` or `X-Atlassian-Webhook-Signature`).
    Returns `(raw_body_bytes, headers_dict)`.
    """
    raw_body = _serialize_payload_bytes(payload)
    signature = _compute_jira_signature(secret, raw_body, prefix=prefix)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Atlassian-Webhook-Identifier": delivery_id or f"jira-del-{uuid.uuid4().hex[:12]}",
        "X-Atlassian-Webhook-Event": webhook_event,
        header_name: signature,
    }
    return raw_body, headers


def sign_slack_payload(
    secret: str,
    payload: bytes | str | dict[str, Any],
    *,
    timestamp: int | float | str | None = None,
    version: str = "v0",
) -> tuple[bytes, dict[str, str]]:
    """
    Signs a webhook payload like Slack (`X-Slack-Signature: v0=<hex>` + `X-Slack-Request-Timestamp`).
    Returns `(raw_body_bytes, headers_dict)`.
    """
    raw_body = _serialize_payload_bytes(payload)
    ts_str = str(int(time.time())) if timestamp is None else str(timestamp)
    signature = _compute_slack_signature(secret, ts_str, raw_body, version=version)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": ts_str,
        "X-Slack-Signature": signature,
    }
    return raw_body, headers


def sign_generic_payload(
    secret: str,
    payload: bytes | str | dict[str, Any],
    *,
    delivery_id: str | None = None,
    header_name: str = "X-Hub-Signature-256",
    prefix: bool = True,
) -> tuple[bytes, dict[str, str]]:
    """
    Signs a webhook payload for the generic provider (`X-Hub-Signature-256` or `X-Webhook-Signature`).
    Returns `(raw_body_bytes, headers_dict)`.
    """
    raw_body = _serialize_payload_bytes(payload)
    signature = _compute_generic_signature(secret, raw_body, prefix=prefix)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Delivery-Id": delivery_id or f"gen-del-{uuid.uuid4().hex[:12]}",
        header_name: signature,
    }
    return raw_body, headers


def sign_payload_for_provider(
    provider: str,
    secret: str,
    payload: bytes | str | dict[str, Any],
    **kwargs: Any,
) -> tuple[bytes, dict[str, str]]:
    """
    Unified dispatcher that signs `payload` using the specified provider's signing scheme.
    Supported providers: `github`, `jira`, `slack`, `generic`.
    """
    prov = provider.strip().lower()
    if prov == "github":
        return sign_github_payload(secret, payload, **kwargs)
    if prov == "jira":
        return sign_jira_payload(secret, payload, **kwargs)
    if prov == "slack":
        return sign_slack_payload(secret, payload, **kwargs)
    if prov == "generic":
        return sign_generic_payload(secret, payload, **kwargs)
    raise ValueError(f"Unsupported provider for payload signing: {provider}")


# ============================================================================
# Test Fixtures & Database Provisioning Helpers
# ============================================================================


async def _register_tenant(label: str) -> uuid.UUID:
    """Registers an isolated tenant via `/tenants/register` and returns its UUID."""
    unique_suffix = uuid.uuid4().hex[:8]
    slug = f"{label.lower().replace(' ', '-')}-{unique_suffix}"[:60]
    reg_payload = {
        "name": f"{label} {unique_suffix}",
        "slug": slug,
        "admin_email": f"admin@{unique_suffix}.example.org",
        "admin_name": f"Admin {unique_suffix}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_res = await ac.post("/tenants/register", json=reg_payload)
        assert reg_res.status_code == 201, reg_res.text
        return uuid.UUID(reg_res.json()["tenant_id"])


async def _create_integration(
    tenant_id: uuid.UUID,
    provider: str,
    raw_secret: str,
    *,
    is_active: bool = True,
    encrypt_secret: bool = True,
) -> uuid.UUID:
    """Inserts an integration record for `tenant_id` with an encrypted (or plaintext) webhook secret."""
    integration_id = uuid.uuid4()
    stored_secret = encryption_service.encrypt(raw_secret) if encrypt_secret else raw_secret
    async with get_tenant_db_context(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO integrations ("
                "  id, tenant_id, provider, external_account_id, webhook_secret_hash, "
                "  is_active, created_at, updated_at"
                ") VALUES (:id, :tenant_id, :provider, :ext_acc, :wh_secret, :is_active, NOW(), NOW())"
            ),
            {
                "id": integration_id,
                "tenant_id": tenant_id,
                "provider": provider,
                "ext_acc": f"{provider}-acct-{uuid.uuid4().hex[:8]}",
                "wh_secret": stored_secret,
                "is_active": is_active,
            },
        )
        await session.commit()
    return integration_id


async def _count_staged_events(
    tenant_id: uuid.UUID,
    provider: str | None = None,
    integration_id: uuid.UUID | None = None,
) -> int:
    """Returns the number of rows in `webhook_events` for `tenant_id` under RLS context."""
    clauses = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if provider is not None:
        clauses.append("provider = :provider")
        params["provider"] = provider
    if integration_id is not None:
        clauses.append("integration_id = :integration_id")
        params["integration_id"] = integration_id

    where_sql = " AND ".join(clauses)
    async with get_tenant_db_context(tenant_id) as session:
        res = await session.execute(
            text(f"SELECT COUNT(*) FROM webhook_events WHERE {where_sql}"),
            params,
        )
        return int(res.scalar_one())


# ============================================================================
# 2. Unit Tests for Provider Signing Helpers & Verification Functions
# ============================================================================


def test_provider_signing_helpers_round_trip():
    """
    SCRUM-328 Scope 1:
    Verifies that `sign_github_payload`, `sign_jira_payload`, `sign_slack_payload`,
    `sign_generic_payload`, and `sign_payload_for_provider` produce authentic headers
    accepted by `verify_signature_for_provider`.
    """
    # 1. GitHub signing helper
    gh_secret = "gh_helper_secret_123"
    gh_body, gh_headers = sign_payload_for_provider(
        "github",
        gh_secret,
        {"ref": "refs/heads/main", "commits": [{"id": "1a2b3c"}]},
    )
    assert gh_headers["X-Hub-Signature-256"].startswith("sha256=")
    verify_signature_for_provider("github", gh_secret, gh_body, gh_headers)

    # 2. Jira signing helper (both X-Hub-Signature-256 and X-Atlassian-Webhook-Signature)
    jira_secret = "jira_helper_secret_456"
    for hdr_name in ("X-Hub-Signature-256", "X-Atlassian-Webhook-Signature"):
        jira_body, jira_headers = sign_payload_for_provider(
            "jira",
            jira_secret,
            {"webhookEvent": "jira:issue_created", "issue": {"key": "SCRUM-328"}},
            header_name=hdr_name,
        )
        assert hdr_name in jira_headers
        verify_signature_for_provider("jira", jira_secret, jira_body, jira_headers)

    # 3. Slack signing helper
    slack_secret = "slack_helper_secret_789"
    slack_body, slack_headers = sign_payload_for_provider(
        "slack",
        slack_secret,
        {"type": "event_callback", "event_id": "EvHelper01", "event": {"type": "app_mention"}},
    )
    assert slack_headers["X-Slack-Signature"].startswith("v0=")
    assert "X-Slack-Request-Timestamp" in slack_headers
    verify_signature_for_provider("slack", slack_secret, slack_body, slack_headers)

    # 4. Generic signing helper
    gen_secret = "generic_helper_secret_000"
    gen_body, gen_headers = sign_payload_for_provider(
        "generic",
        gen_secret,
        {"event": "custom_build"},
    )
    verify_signature_for_provider("generic", gen_secret, gen_body, gen_headers)


def test_verifiers_use_constant_time_hmac_compare_digest():
    """
    Security Invariant (SCRUM-325 & SCRUM-326):
    All signature verifiers must compare MACs using constant-time `hmac.compare_digest`.
    """
    secret = "constant_time_secret_key"
    payload = {"check": "constant-time"}

    with patch.object(
        webhook_verification.hmac, "compare_digest", wraps=hmac.compare_digest
    ) as spy_cmp:
        gh_body, gh_headers = sign_github_payload(secret, payload)
        verify_github_signature(secret, gh_body, gh_headers)

        jira_body, jira_headers = sign_jira_payload(secret, payload)
        verify_jira_signature(secret, jira_body, jira_headers)

        slack_body, slack_headers = sign_slack_payload(secret, payload)
        verify_slack_signature(secret, slack_body, slack_headers)

        assert spy_cmp.call_count >= 3


# ============================================================================
# 3. GitHub Webhook Authentication Scenarios (SCRUM-325, SCRUM-327, SCRUM-328)
# ============================================================================


@pytest.mark.asyncio
async def test_github_webhook_signature_verification_lifecycle():
    """
    SCRUM-325 & SCRUM-327:
    GitHub webhook delivered to an integration with encrypted secret.
    - Valid signature -> 202
    - Tampered body -> 401
    - Wrong secret -> 401
    - Missing signature -> 401
    """
    tenant_id = await _register_tenant("GH Auth Org")
    raw_secret = "gh_secret_token_abcdef123456"
    integration_id = await _create_integration(tenant_id, "github", raw_secret)

    payload_dict = {"ref": "refs/heads/main", "commits": [{"id": "c123"}]}
    raw_body, valid_headers = sign_github_payload(raw_secret, payload_dict)
    valid_headers["X-Tenant-ID"] = str(tenant_id)

    # Case 1: Valid signature -> 202 Accepted & 1 row staged
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_ok = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=valid_headers,
        )
    assert res_ok.status_code == 202, res_ok.text
    assert res_ok.json()["status"] == "accepted"
    assert await _count_staged_events(tenant_id, "github", integration_id) == 1

    # Case 2: Tampered payload with original signature -> 401 Unauthorized (no new row)
    tampered_body = json.dumps({"ref": "refs/heads/main", "commits": [{"id": "TAMPERED"}]}).encode(
        "utf-8"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_tampered = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=tampered_body,
            headers={
                **valid_headers,
                "X-GitHub-Delivery": str(uuid.uuid4()),
            },
        )
    assert res_tampered.status_code == 401
    assert res_tampered.headers.get("WWW-Authenticate") == "WebhookSignature"
    assert "Invalid GitHub webhook signature" in res_tampered.text
    assert await _count_staged_events(tenant_id, "github", integration_id) == 1

    # Case 3: Signed with wrong secret -> 401 Unauthorized
    _, wrong_secret_headers = sign_github_payload("wrong_gh_secret_999", payload_dict)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_wrong_secret = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=wrong_secret_headers,
        )
    assert res_wrong_secret.status_code == 401
    assert "Invalid GitHub webhook signature" in res_wrong_secret.text
    assert await _count_staged_events(tenant_id, "github", integration_id) == 1

    # Case 4: Missing signature header -> 401 Unauthorized
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_missing = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Tenant-ID": str(tenant_id),
            },
        )
    assert res_missing.status_code == 401
    assert "Missing GitHub webhook signature header" in res_missing.text
    assert await _count_staged_events(tenant_id, "github", integration_id) == 1


@pytest.mark.asyncio
async def test_github_webhook_header_variants_and_unparsed_raw_bytes():
    """
    SCRUM-325 & SCRUM-328:
    Verifies GitHub signature edge cases:
    - Non-canonical JSON formatting (pretty-printed whitespace & Unicode characters) verified
      over exact raw bytes before JSON parsing.
    - Legacy `X-Hub-Signature` header fallback and raw hex digest acceptance.
    - Empty/whitespace or malformed signature header rejection (HTTP 401).
    """
    tenant_id = await _register_tenant("GH Variants Org")
    raw_secret = "gh_raw_bytes_secret_2026"
    integration_id = await _create_integration(tenant_id, "github", raw_secret)

    # 1. Pretty-printed JSON with extra whitespace and Unicode characters
    raw_pretty_bytes = (
        '{\n  "action":   "opened",\n  "title": "Fix 🚀 unicode",\n  "number": 42\n}'.encode(
            "utf-8"
        )
    )
    _, pretty_headers = sign_github_payload(raw_secret, raw_pretty_bytes)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_pretty = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_pretty_bytes,
            headers=pretty_headers,
        )
    assert res_pretty.status_code == 202, res_pretty.text

    # Re-serializing compactly without re-signing must fail (proves raw byte verification)
    compact_bytes = json.dumps(json.loads(raw_pretty_bytes.decode("utf-8"))).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_reformatted = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=compact_bytes,
            headers={**pretty_headers, "X-GitHub-Delivery": str(uuid.uuid4())},
        )
    assert res_reformatted.status_code == 401

    # 2. Legacy `X-Hub-Signature` header & unprefixed hex digest
    legacy_body, legacy_headers = sign_github_payload(
        raw_secret,
        {"action": "closed", "number": 43},
        header_name="X-Hub-Signature",
        prefix=False,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_legacy = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=legacy_body,
            headers=legacy_headers,
        )
    assert res_legacy.status_code == 202, res_legacy.text

    # 3. Whitespace-only signature header -> 401 Missing signature header
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_empty_sig = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=legacy_body,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "X-Hub-Signature-256": "   ",
            },
        )
    assert res_empty_sig.status_code == 401
    assert "Missing GitHub webhook signature header" in res_empty_sig.text


# ============================================================================
# 4. Jira Webhook Authentication Scenarios (SCRUM-325, SCRUM-327, SCRUM-328)
# ============================================================================


@pytest.mark.asyncio
async def test_jira_webhook_signature_verification():
    """
    SCRUM-325 & SCRUM-328:
    Jira webhook HMAC-SHA256 signature verification across all scenario cases:
    - Valid signature via `X-Hub-Signature-256` -> 202 Accepted
    - Valid signature via `X-Atlassian-Webhook-Signature` -> 202 Accepted
    - Tampered body -> 401 Unauthorized
    - Wrong secret -> 401 Unauthorized
    - Missing signature header -> 401 Unauthorized
    """
    tenant_id = await _register_tenant("Jira Auth Org")
    raw_secret = "jira_secret_xyz_789"
    integration_id = await _create_integration(tenant_id, "jira", raw_secret)

    payload_dict = {"webhookEvent": "jira:issue_created", "timestamp": 1730300000}
    raw_body, headers_hub = sign_jira_payload(
        raw_secret,
        payload_dict,
        header_name="X-Hub-Signature-256",
    )

    # Case 1: Valid signature via X-Hub-Signature-256 -> 202 Accepted
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_ok1 = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=headers_hub,
        )
    assert res_ok1.status_code == 202, res_ok1.text
    assert await _count_staged_events(tenant_id, "jira", integration_id) == 1

    # Case 2: Valid signature via X-Atlassian-Webhook-Signature -> 202 Accepted
    raw_body2, headers_atlassian = sign_jira_payload(
        raw_secret,
        {"webhookEvent": "jira:issue_updated", "timestamp": 1730300100},
        header_name="X-Atlassian-Webhook-Signature",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_ok2 = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body2,
            headers=headers_atlassian,
        )
    assert res_ok2.status_code == 202, res_ok2.text
    assert await _count_staged_events(tenant_id, "jira", integration_id) == 2

    # Case 3: Tampered payload body -> 401 Unauthorized
    tampered_jira = json.dumps(
        {"webhookEvent": "jira:issue_deleted", "timestamp": 1730300000}
    ).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_tampered = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=tampered_jira,
            headers={
                **headers_hub,
                "X-Atlassian-Webhook-Identifier": f"del-{uuid.uuid4().hex[:8]}",
            },
        )
    assert res_tampered.status_code == 401
    assert "Invalid Jira webhook signature" in res_tampered.text
    assert await _count_staged_events(tenant_id, "jira", integration_id) == 2

    # Case 4: Wrong Jira secret -> 401 Unauthorized
    _, wrong_jira_headers = sign_jira_payload("wrong_jira_secret_000", payload_dict)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_wrong = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=wrong_jira_headers,
        )
    assert res_wrong.status_code == 401
    assert "Invalid Jira webhook signature" in res_wrong.text

    # Case 5: Missing signature header -> 401 Unauthorized
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_missing = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Atlassian-Webhook-Identifier": f"del-{uuid.uuid4().hex[:8]}",
            },
        )
    assert res_missing.status_code == 401
    assert "Missing Jira webhook signature header" in res_missing.text
    assert await _count_staged_events(tenant_id, "jira", integration_id) == 2


# ============================================================================
# 5. Slack Signature & Replay Window Scenarios (SCRUM-326, SCRUM-328)
# ============================================================================


@pytest.mark.asyncio
async def test_slack_signature_and_replay_window_enforcement():
    """
    SCRUM-326 & SCRUM-328:
    Slack webhooks enforce HMAC-SHA256 signature (`v0=<hex>`) AND a strict 5-minute replay window.
    - Valid recent request (<= 300s) -> 202 Accepted
    - Expired past request (> 300s ago) -> 401 (replay attack detected)
    - Future-dated request (> 300s ahead) -> 401 (replay attack detected)
    - Timestamp swap attack (fresh timestamp header with old signature) -> 401
    - Tampered body or wrong secret -> 401
    - Missing/malformed timestamp or signature headers -> 401
    """
    tenant_id = await _register_tenant("Slack Auth Org")
    raw_secret = "slack_signing_secret_998877"
    integration_id = await _create_integration(tenant_id, "slack", raw_secret)

    payload_dict = {"event_id": f"Ev_{uuid.uuid4().hex[:10]}", "event": {"text": "hello"}}

    # Case 1: Fresh request (timestamp = now) -> 202 Accepted
    now_ts = int(time.time())
    raw_body, fresh_headers = sign_slack_payload(raw_secret, payload_dict, timestamp=now_ts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_fresh = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=fresh_headers,
        )
    assert res_fresh.status_code == 202, res_fresh.text
    assert res_fresh.json()["status"] == "accepted"
    assert await _count_staged_events(tenant_id, "slack", integration_id) == 1

    # Case 2: Replay attack — timestamp is 305 seconds old (> 5 mins) -> 401
    old_ts = int(time.time()) - (SLACK_MAX_REPLAY_AGE_SECONDS + 5)
    _, old_headers = sign_slack_payload(raw_secret, payload_dict, timestamp=old_ts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_replayed = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=old_headers,
        )
    assert res_replayed.status_code == 401
    assert "replay attack detected" in res_replayed.text
    assert await _count_staged_events(tenant_id, "slack", integration_id) == 1

    # Case 3: Future timestamp beyond 5-minute window (> +300s) -> 401
    future_ts = int(time.time()) + (SLACK_MAX_REPLAY_AGE_SECONDS + 10)
    _, future_headers = sign_slack_payload(raw_secret, payload_dict, timestamp=future_ts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_future = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=future_headers,
        )
    assert res_future.status_code == 401
    assert "replay attack detected" in res_future.text
    assert await _count_staged_events(tenant_id, "slack", integration_id) == 1

    # Case 4: Timestamp header tampering (attacker updates expired timestamp to `now`
    # while keeping the captured `X-Slack-Signature` from `old_ts`) -> 401
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_ts_swap = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": old_headers["X-Slack-Signature"],
            },
        )
    assert res_ts_swap.status_code == 401
    assert "Invalid Slack webhook signature" in res_ts_swap.text

    # Case 5: Tampered signature on fresh timestamp -> 401
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_bad_sig = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(now_ts),
                "X-Slack-Signature": "v0=invalid_bad_signature_hex_123456",
            },
        )
    assert res_bad_sig.status_code == 401
    assert "Invalid Slack webhook signature" in res_bad_sig.text

    # Case 6: Tampered body with valid fresh signature -> 401
    tampered_slack_body = json.dumps(
        {"event_id": f"Ev_{uuid.uuid4().hex[:10]}", "event": {"text": "tampered"}}
    ).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_tampered_body = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=tampered_slack_body,
            headers=fresh_headers,
        )
    assert res_tampered_body.status_code == 401
    assert "Invalid Slack webhook signature" in res_tampered_body.text

    # Case 7: Missing headers & malformed timestamp -> 401
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Missing X-Slack-Signature
        res_no_sig = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={"X-Slack-Request-Timestamp": str(now_ts)},
        )
        assert res_no_sig.status_code == 401
        assert "Missing Slack signature or timestamp header" in res_no_sig.text

        # Missing X-Slack-Request-Timestamp
        res_no_ts = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={"X-Slack-Signature": fresh_headers["X-Slack-Signature"]},
        )
        assert res_no_ts.status_code == 401
        assert "Missing Slack signature or timestamp header" in res_no_ts.text

        # Non-numeric timestamp format
        res_bad_ts = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers={
                "X-Slack-Request-Timestamp": "not-a-unix-timestamp",
                "X-Slack-Signature": fresh_headers["X-Slack-Signature"],
            },
        )
        assert res_bad_ts.status_code == 401
        assert "Invalid Slack timestamp format" in res_bad_ts.text

        # Unsupported version prefix (`v1=...`)
        _, v1_headers = sign_slack_payload(raw_secret, payload_dict, timestamp=now_ts, version="v1")
        res_v1 = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=v1_headers,
        )
        assert res_v1.status_code == 401
        assert "Invalid Slack webhook signature" in res_v1.text

    assert await _count_staged_events(tenant_id, "slack", integration_id) == 1


def test_slack_replay_window_exact_300_second_boundary():
    """
    SCRUM-326:
    Deterministic boundary check for the 5-minute (300.0s) Slack replay window:
    - Exactly `now - 300s` and `now + 300s` -> accepted.
    - `now - 301s` and `now + 301s` -> rejected with HTTPException(401).
    """
    fixed_now = 1_750_000_000.0
    secret = "slack_boundary_secret_key"
    payload = {"type": "event_callback", "event_id": "EvBoundary"}

    with patch("app.webhooks.verification.time.time", return_value=fixed_now):
        # 1. Exact lower and upper 300s boundaries pass
        for valid_ts in (int(fixed_now - 300), int(fixed_now), int(fixed_now + 300)):
            body, headers = sign_slack_payload(secret, payload, timestamp=valid_ts)
            verify_slack_signature(secret, body, headers)

        # 2. 1 second past the 300s boundary fails with 401
        for expired_ts in (int(fixed_now - 301), int(fixed_now + 301)):
            body, headers = sign_slack_payload(secret, payload, timestamp=expired_ts)
            with pytest.raises(HTTPException) as exc_info:
                verify_slack_signature(secret, body, headers)
            assert exc_info.value.status_code == 401
            assert "replay attack detected" in str(exc_info.value.detail)


# ============================================================================
# 6. Provider-Level Webhook Authentication (`/api/v1/webhooks/{provider}`)
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "jira", "slack", "generic"])
async def test_provider_level_endpoint_signature_and_replay_scenarios(provider: str):
    """
    SCRUM-328 Scope 2:
    Verifies signature and replay scenarios on `/api/v1/webhooks/{provider}` when
    `X-Webhook-Secret` is supplied:
    - Valid provider signature -> HTTP 202 Accepted & 1 row staged.
    - Tampered body or wrong secret -> HTTP 401 Unauthorized & no additional row staged.
    """
    tenant_id = await _register_tenant(f"ProvAuth {provider}")
    secret = f"prov_secret_{provider}_2026"
    payload = {
        "event_id": f"EvProv_{uuid.uuid4().hex[:10]}",
        "webhookEvent": "jira:issue_updated",
        "action": "synchronize",
    }

    raw_body, signed_headers = sign_payload_for_provider(provider, secret, payload)
    headers = {
        **signed_headers,
        "X-Tenant-ID": str(tenant_id),
        "X-Webhook-Secret": secret,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Valid signature -> 202 Accepted
        res_ok = await ac.post(
            f"/api/v1/webhooks/{provider}",
            content=raw_body,
            headers=headers,
        )
        assert res_ok.status_code == 202, res_ok.text
        assert await _count_staged_events(tenant_id, provider) == 1

        # 2. Tampered body -> 401 Unauthorized
        tampered_body = json.dumps({**payload, "tampered": True}).encode("utf-8")
        res_bad = await ac.post(
            f"/api/v1/webhooks/{provider}",
            content=tampered_body,
            headers=headers,
        )
        assert res_bad.status_code == 401
        assert await _count_staged_events(tenant_id, provider) == 1


# ============================================================================
# 7. Integration Routing, Tenant Isolation & Inactive Rejection (SCRUM-327)
# ============================================================================


@pytest.mark.asyncio
async def test_unknown_and_inactive_integration_rejection():
    """
    SCRUM-327:
    - Unknown integration UUID returns HTTP 404 without leaking tenant info.
    - Inactive integration (`is_active = false`) returns HTTP 404 even when signed with valid secret.
    """
    # 1. Unknown integration ID -> 404
    dummy_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_unknown = await ac.post(
            f"/api/v1/webhooks/integrations/{dummy_id}",
            json={"test": True},
        )
    assert res_unknown.status_code == 404
    assert res_unknown.json()["detail"] == "Integration not found or inactive"

    # 2. Inactive integration -> 404 even with valid signature
    tenant_id = await _register_tenant("Inactive Integ Org")
    secret = "inactive_integration_secret_123"
    inactive_id = await _create_integration(tenant_id, "github", secret, is_active=False)
    raw_body, headers = sign_github_payload(secret, {"ref": "refs/heads/main"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_inactive = await ac.post(
            f"/api/v1/webhooks/integrations/{inactive_id}",
            content=raw_body,
            headers=headers,
        )
    assert res_inactive.status_code == 404
    assert res_inactive.json()["detail"] == "Integration not found or inactive"
    assert await _count_staged_events(tenant_id, "github", inactive_id) == 0


@pytest.mark.asyncio
async def test_cross_tenant_secret_isolation_and_spoofed_tenant_header_ignored():
    """
    SCRUM-327:
    1. Signing a webhook with Tenant B's integration secret and sending it to Tenant A's
       integration URL fails with HTTP 401 and stores 0 rows.
    2. Sending a valid webhook to Tenant A's integration URL with a spoofed `X-Tenant-ID`
       pointing to Tenant B still resolves strictly to Tenant A (0 rows in Tenant B).
    """
    tenant_a = await _register_tenant("Tenant A Iso")
    tenant_b = await _register_tenant("Tenant B Iso")

    secret_a = "tenant_a_exclusive_secret_111"
    secret_b = "tenant_b_exclusive_secret_222"
    integ_a = await _create_integration(tenant_a, "github", secret_a)
    await _create_integration(tenant_b, "github", secret_b)

    payload = {"ref": "refs/heads/main", "repository": {"full_name": "tenant-a/repo"}}

    # 1. Signed with Tenant B's secret, sent to Tenant A's integration -> 401
    body_b, headers_b = sign_github_payload(secret_b, payload)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_cross_secret = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_a}",
            content=body_b,
            headers=headers_b,
        )
    assert res_cross_secret.status_code == 401
    assert await _count_staged_events(tenant_a) == 0
    assert await _count_staged_events(tenant_b) == 0

    # 2. Signed with Tenant A's secret, but attacker passes spoofed `X-Tenant-ID: <tenant_b>`
    body_a, headers_a = sign_github_payload(secret_a, payload)
    headers_a["X-Tenant-ID"] = str(tenant_b)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_spoofed = await ac.post(
            f"/api/v1/webhooks/integrations/{integ_a}",
            content=body_a,
            headers=headers_a,
        )
    assert res_spoofed.status_code == 202
    assert await _count_staged_events(tenant_a, "github", integ_a) == 1
    assert await _count_staged_events(tenant_b) == 0


# ============================================================================
# 8. Database Outage Resilience (SCRUM-331)
# ============================================================================


@pytest.mark.asyncio
async def test_postgresql_unavailable_returns_http_503():
    """
    SCRUM-331:
    When PostgreSQL is down or staging raises a database error:
    - Must return HTTP 503 Service Unavailable.
    - Must include 'Retry-After: 30' header.
    - Must NEVER send HTTP 202 in this failure path.
    """
    with patch(
        "app.webhooks.router.stage_webhook_event_idempotent",
        side_effect=OperationalError("Connection to server at 'localhost:5432' failed", None, None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/webhooks/github",
                json={"ref": "refs/heads/main"},
                headers={"X-GitHub-Delivery": str(uuid.uuid4())},
            )

        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "30"
        assert "Database storage service unavailable" in response.text

    # Also verify integration endpoint returns 503 when route lookup fails with DB error
    with patch(
        "sqlalchemy.ext.asyncio.AsyncSession.execute",
        side_effect=OperationalError("Connection refused to database pool", None, None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res_integ_503 = await ac.post(
                f"/api/v1/webhooks/integrations/{uuid.uuid4()}",
                json={"ref": "refs/heads/main"},
            )
        assert res_integ_503.status_code == 503
        assert res_integ_503.headers.get("Retry-After") == "30"
        assert "Database storage service unavailable" in res_integ_503.text


# ============================================================================
# 9. SCRUM-328 AC 2: Removing Signature Verification Makes Tests Fail
# ============================================================================


@pytest.mark.asyncio
async def test_removing_signature_verification_makes_tests_fail(monkeypatch: pytest.MonkeyPatch):
    """
    SCRUM-328 Acceptance Criterion 2:
    "Removing signature verification makes tests fail."

    Proves two invariants:
    1. When signature verification is active, unsigned, tampered, wrong-secret, and replayed
       webhooks across GitHub, Jira, and Slack are rejected with HTTP 401 and store 0 rows.
    2. If signature verification is removed/disabled (either by making `verify_signature_for_provider`
       a no-op in `app.webhooks.router` or by disabling `verify_github_signature`,
       `verify_jira_signature`, and `verify_slack_signature` in `app.webhooks.verification`),
       those same tampered/unsigned/replayed requests erroneously return HTTP 202 and persist
       unauthorized rows, causing the test suite's `status_code == 401` and `row_count == 0`
       assertions to fail with `AssertionError`.
    """
    tenant_id = await _register_tenant("RemoveSigGuardrail")
    gh_secret = "guardrail_gh_secret"
    jira_secret = "guardrail_jira_secret"
    slack_secret = "guardrail_slack_secret"

    gh_integ = await _create_integration(tenant_id, "github", gh_secret)
    jira_integ = await _create_integration(tenant_id, "jira", jira_secret)
    slack_integ = await _create_integration(tenant_id, "slack", slack_secret)

    # Craft invalid/attack requests that MUST be rejected when signature verification is present:
    # (a) GitHub tampered payload with forged signature
    gh_attack_body = json.dumps({"ref": "refs/heads/main", "forged": True}).encode("utf-8")
    gh_attack_headers = {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": f"forged-gh-{uuid.uuid4().hex[:8]}",
        "X-Hub-Signature-256": "sha256=" + ("0" * 64),
    }

    # (b) Jira unsigned request (missing signature header)
    jira_attack_body = json.dumps(
        {"webhookEvent": "jira:issue_deleted", "issue": {"key": "SCRUM-328"}}
    ).encode("utf-8")
    jira_attack_headers = {
        "Content-Type": "application/json",
        "X-Atlassian-Webhook-Identifier": f"forged-jira-{uuid.uuid4().hex[:8]}",
    }

    # (c) Slack replay attack (timestamp expired 10 minutes ago)
    expired_ts = int(time.time()) - 600
    slack_attack_body, slack_attack_headers = sign_slack_payload(
        slack_secret,
        {"type": "event_callback", "event_id": f"EvReplay_{uuid.uuid4().hex[:8]}"},
        timestamp=expired_ts,
    )

    async def _assert_attack_requests_rejected() -> None:
        """Runs the standard webhook authentication test assertions."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r_gh = await ac.post(
                f"/api/v1/webhooks/integrations/{gh_integ}",
                content=gh_attack_body,
                headers=gh_attack_headers,
            )
            r_jira = await ac.post(
                f"/api/v1/webhooks/integrations/{jira_integ}",
                content=jira_attack_body,
                headers=jira_attack_headers,
            )
            r_slack = await ac.post(
                f"/api/v1/webhooks/integrations/{slack_integ}",
                content=slack_attack_body,
                headers=slack_attack_headers,
            )
            r_prov = await ac.post(
                "/api/v1/webhooks/github",
                content=gh_attack_body,
                headers={
                    **gh_attack_headers,
                    "X-Tenant-ID": str(tenant_id),
                    "X-Webhook-Secret": gh_secret,
                },
            )

        assert r_gh.status_code == 401, (
            f"Expected 401 for forged GitHub webhook, got {r_gh.status_code}"
        )
        assert r_jira.status_code == 401, (
            f"Expected 401 for unsigned Jira webhook, got {r_jira.status_code}"
        )
        assert r_slack.status_code == 401, (
            f"Expected 401 for replayed Slack webhook, got {r_slack.status_code}"
        )
        assert r_prov.status_code == 401, (
            f"Expected 401 for forged provider webhook, got {r_prov.status_code}"
        )
        assert await _count_staged_events(tenant_id) == 0

    # Baseline: With signature verification enabled, all attack assertions pass
    await _assert_attack_requests_rejected()

    # Simulation 1: Remove provider verifiers inside `app.webhooks.verification`
    with (
        patch.object(webhook_verification, "verify_github_signature", lambda *a, **kw: None),
        patch.object(webhook_verification, "verify_jira_signature", lambda *a, **kw: None),
        patch.object(webhook_verification, "verify_slack_signature", lambda *a, **kw: None),
    ):
        with pytest.raises(AssertionError, match="Expected 401"):
            await _assert_attack_requests_rejected()

    # Simulation 2: Remove `verify_signature_for_provider` call in `app.webhooks.router`
    with patch.object(webhook_router, "verify_signature_for_provider", lambda *a, **kw: None):
        with pytest.raises(AssertionError, match="Expected 401"):
            await _assert_attack_requests_rejected()

    # Clean up the unauthorized rows staged during the removal simulations
    async with get_tenant_db_context(tenant_id) as session:
        await session.execute(
            text("DELETE FROM webhook_events WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await session.commit()

    # Confirm that once signature verification is restored, all assertions pass again
    await _assert_attack_requests_rejected()
