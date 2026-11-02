"""
SCRUM-335 / SCRUM-336: Comprehensive test suite for delivery identifier extraction and
idempotent webhook duplicate-delivery scenarios across GitHub, Jira, and Slack
(see docs/webhook-duplicate-test-scenarios.md).

Verifies:
1. Delivery ID extraction across GitHub, Jira, Slack, and generic providers.
2. Deterministic SHA-256 fallback when explicit delivery ID is missing.
3. Unauthenticated access on /api/v1/webhooks/* (public prefix in JWTAuthMiddleware).
4. Same event twice per provider -> 1st HTTP 202 (duplicate=False), 2nd HTTP 202 (duplicate=True), 1 row stored.
5. Same ID in different tenants per provider -> both stored (1 row in Tenant A, 1 row in Tenant B, 2 rows total).
6. Same ID in different providers -> both/all stored (2 rows pairwise, 3 rows across GitHub + Jira + Slack).
7. Missing delivery ID per provider -> secondary attribute fallback & deterministic SHA-256 digest row counts.
8. Integration-bound webhook intake (/api/v1/webhooks/integrations/{id}) including cross-tenant duplicate isolation.
9. Unsupported providers return HTTP 400.
"""

import hashlib
import hmac
import json
import time
from typing import Any
import uuid
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text

from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
from app.webhooks.delivery import extract_delivery_id


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections and reset registration rate limiter per test."""
    registration_rate_limiter.reset()
    yield
    registration_rate_limiter.reset()
    await async_engine.dispose()


def test_extract_delivery_id_github():
    # 1. Official GitHub header
    headers = {"X-GitHub-Delivery": "72d3162e-cc78-11e3-81ab-4c9367dc0958"}
    assert (
        extract_delivery_id("github", headers, payload={}) == "72d3162e-cc78-11e3-81ab-4c9367dc0958"
    )

    # 2. Payload fallback
    assert (
        extract_delivery_id("github", {}, payload={"delivery_id": "custom-gh-delivery-123"})
        == "custom-gh-delivery-123"
    )


def test_extract_delivery_id_jira():
    # 1. Jira Cloud header
    headers = {"X-Atlassian-Webhook-Identifier": "jira-hook-id-998877"}
    assert extract_delivery_id("jira", headers, payload={}) == "jira-hook-id-998877"

    # 2. Jira payload fallback with event + timestamp
    payload = {"webhookEvent": "jira:issue_updated", "timestamp": 1730300000}
    assert extract_delivery_id("jira", {}, payload=payload) == "jira-jira:issue_updated-1730300000"

    # 3. Jira payload eventId
    assert extract_delivery_id("jira", {}, payload={"eventId": "evt-4455"}) == "evt-4455"


def test_extract_delivery_id_slack():
    # 1. Slack payload event_id
    payload = {"event_id": "Ev024BE92L", "type": "event_callback"}
    assert extract_delivery_id("slack", {}, payload=payload) == "Ev024BE92L"

    # 2. Slack header fallback
    headers = {"X-Slack-Request-Timestamp": "1730301234", "X-Slack-Retry-Num": "1"}
    assert extract_delivery_id("slack", headers, payload={}) == "slack-1730301234-r1"


def test_extract_delivery_id_fallback_digest():
    payload_a = {"event": "push", "ref": "refs/heads/main"}
    payload_b = {"event": "pull_request", "ref": "refs/heads/develop"}

    id_a1 = extract_delivery_id("github", {}, payload=payload_a)
    id_a2 = extract_delivery_id("github", {}, payload=payload_a)
    id_b = extract_delivery_id("github", {}, payload=payload_b)

    assert id_a1.startswith("gen-github-")
    assert id_a1 == id_a2, "Identical payloads must yield identical deterministic digests"
    assert id_a1 != id_b, "Different payloads must yield different fallback digests"


@pytest.mark.asyncio
async def test_webhook_intake_unsupported_provider():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/webhooks/unsupported-system", json={"test": True})
    assert response.status_code == 400
    assert "Unsupported webhook provider" in response.text


@pytest.mark.asyncio
async def test_webhook_github_idempotency_lifecycle():
    """
    Simulates GitHub sending a webhook, followed by an immediate redelivery/retry.
    Asserts:
    1. First delivery -> HTTP 202, duplicate=False.
    2. Redelivery -> HTTP 202, duplicate=True.
    3. Exactly 1 row in webhook_events table in DB.
    """
    # 1. Provision a real tenant to associate the webhook with
    unique_suffix = uuid.uuid4().hex[:8]
    slug = f"org-{unique_suffix}"
    reg_payload = {
        "name": f"Org {unique_suffix}",
        "slug": slug,
        "admin_email": f"admin@{unique_suffix}.com",
        "admin_name": f"Admin {unique_suffix}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_res = await ac.post("/tenants/register", json=reg_payload)
        assert reg_res.status_code == 201, reg_res.text
        tenant_id = uuid.UUID(reg_res.json()["tenant_id"])

    github_delivery_id = str(uuid.uuid4())
    headers = {
        "X-GitHub-Delivery": github_delivery_id,
        "X-GitHub-Event": "push",
        "X-Tenant-ID": str(tenant_id),
    }
    webhook_body = {
        "ref": "refs/heads/main",
        "repository": {"full_name": "acme/backend"},
        "pusher": {"name": "developer"},
    }

    # First Delivery
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res1 = await ac.post(
            "/api/v1/webhooks/github",
            json=webhook_body,
            headers=headers,
        )

    assert res1.status_code == 202, res1.text
    data1 = res1.json()
    assert data1["status"] == "accepted"
    assert data1["duplicate"] is False
    assert data1["provider"] == "github"
    assert data1["delivery_id"] == github_delivery_id
    assert data1["event_id"] is not None

    # Redelivery (Same delivery ID and headers)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res2 = await ac.post(
            "/api/v1/webhooks/github",
            json=webhook_body,
            headers=headers,
        )

    assert res2.status_code == 202, res2.text
    data2 = res2.json()
    assert data2["status"] == "accepted"
    assert data2["duplicate"] is True, "Second delivery must be identified as duplicate"
    assert data2["provider"] == "github"
    assert data2["delivery_id"] == github_delivery_id
    assert data2["event_id"] == data1["event_id"], "Duplicate must resolve to the existing event_id"

    # Direct DB verification: ensure no duplicate rows created
    async with get_tenant_db_context(tenant_id) as session:
        rows_res = await session.execute(
            text(
                "SELECT id, provider, provider_delivery_id, status FROM webhook_events "
                "WHERE provider = 'github' AND provider_delivery_id = :del_id"
            ),
            {"del_id": github_delivery_id},
        )
        rows = rows_res.mappings().all()
        assert len(rows) == 1, f"Expected exactly 1 staged event row, found {len(rows)}"
        assert rows[0]["provider_delivery_id"] == github_delivery_id
        assert rows[0]["status"] == "staged"


@pytest.mark.asyncio
async def test_webhook_jira_and_slack_idempotency():
    """
    Verifies Jira and Slack intake and duplicate detection.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    reg_payload = {
        "name": f"Jira Slack Org {unique_suffix}",
        "slug": f"js-{unique_suffix}",
        "admin_email": f"admin@{unique_suffix}.net",
        "admin_name": f"Admin {unique_suffix}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_res = await ac.post("/tenants/register", json=reg_payload)
        assert reg_res.status_code == 201, reg_res.text
        tenant_id = uuid.UUID(reg_res.json()["tenant_id"])

    # Jira test
    jira_id = f"jira-delivery-{uuid.uuid4().hex[:12]}"
    jira_headers = {
        "X-Atlassian-Webhook-Identifier": jira_id,
        "X-Tenant-ID": str(tenant_id),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        j1 = await ac.post(
            "/api/v1/webhooks/jira",
            json={"issue": {"key": "SCRUM-335"}},
            headers=jira_headers,
        )
        assert j1.status_code == 202
        assert j1.json()["duplicate"] is False

        j2 = await ac.post(
            "/api/v1/webhooks/jira",
            json={"issue": {"key": "SCRUM-335"}},
            headers=jira_headers,
        )
        assert j2.status_code == 202
        assert j2.json()["duplicate"] is True

    # Slack test (with event_id in payload)
    slack_evt_id = f"Ev_{uuid.uuid4().hex[:10]}"
    slack_payload = {
        "event_id": slack_evt_id,
        "event": {"type": "app_mention", "text": "ping"},
    }
    slack_headers = {"X-Tenant-ID": str(tenant_id)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        s1 = await ac.post(
            "/api/v1/webhooks/slack",
            json=slack_payload,
            headers=slack_headers,
        )
        assert s1.status_code == 202
        assert s1.json()["duplicate"] is False

        s2 = await ac.post(
            "/api/v1/webhooks/slack",
            json=slack_payload,
            headers=slack_headers,
        )
        assert s2.status_code == 202
        assert s2.json()["duplicate"] is True


@pytest.mark.asyncio
async def test_integration_bound_webhook_intake():
    """
    Verifies POST /api/v1/webhooks/integrations/{integration_id} resolves tenant_id
    and stages webhooks with idempotency.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    reg_payload = {
        "name": f"Integration Org {unique_suffix}",
        "slug": f"integ-{unique_suffix}",
        "admin_email": f"integ@{unique_suffix}.io",
        "admin_name": f"Admin {unique_suffix}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_res = await ac.post("/tenants/register", json=reg_payload)
        assert reg_res.status_code == 201, reg_res.text
        tenant_id = uuid.UUID(reg_res.json()["tenant_id"])

    integration_id = uuid.uuid4()
    raw_secret = "whsec_super_secret_github_token"
    secret_encrypted = encryption_service.encrypt(raw_secret)

    # Insert active integration record under tenant
    async with get_tenant_db_context(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO integrations ("
                "  id, tenant_id, provider, external_account_id, webhook_secret_hash, is_active, created_at, updated_at"
                ") VALUES (:id, :tenant_id, :provider, :ext_acc, :wh_secret, true, NOW(), NOW())"
            ),
            {
                "id": integration_id,
                "tenant_id": tenant_id,
                "provider": "github",
                "ext_acc": f"org-account-{unique_suffix}",
                "wh_secret": secret_encrypted,
            },
        )
        await session.commit()

    delivery_id = str(uuid.uuid4())
    payload = {"action": "opened", "issue": {"number": 42}}
    raw_body = json.dumps(payload).encode("utf-8")
    mac = hmac.new(raw_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": f"sha256={mac}",
        "X-Tenant-ID": str(tenant_id),
    }

    # First call: stages new webhook
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res1 = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=headers,
        )
        assert res1.status_code == 202, res1.text
        assert res1.json()["duplicate"] is False
        assert res1.json()["delivery_id"] == delivery_id

        # Redelivery call: detected duplicate
        res2 = await ac.post(
            f"/api/v1/webhooks/integrations/{integration_id}",
            content=raw_body,
            headers=headers,
        )
        assert res2.status_code == 202, res2.text
        assert res2.json()["duplicate"] is True

    # Non-existent integration returns 404
    dummy_integration_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_404 = await ac.post(
            f"/api/v1/webhooks/integrations/{dummy_integration_id}",
            content=raw_body,
            headers=headers,
        )
        assert res_404.status_code == 404


# ============================================================================
# SCRUM-336: Provider Duplicate-Delivery Scenarios (GitHub, Jira, Slack)
# ============================================================================


async def _provision_test_tenant(label: str) -> uuid.UUID:
    """Helper to register an isolated tenant for webhook duplicate scenario tests."""
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "name": f"{label} {suffix}",
        "slug": f"{label.lower().replace(' ', '-')}-{suffix}"[:60],
        "admin_email": f"admin@{suffix}.example.com",
        "admin_name": f"Admin {suffix}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/tenants/register", json=payload)
        assert res.status_code == 201, res.text
        return uuid.UUID(res.json()["tenant_id"])


def _build_provider_webhook_request(
    provider: str,
    delivery_id: str,
    tenant_id: uuid.UUID,
    seq: int = 1,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Builds provider-specific headers and JSON payload for explicit delivery_id tests."""
    headers: dict[str, str] = {"X-Tenant-ID": str(tenant_id)}
    if provider == "github":
        headers["X-GitHub-Delivery"] = delivery_id
        headers["X-GitHub-Event"] = "push"
        payload: dict[str, Any] = {
            "ref": "refs/heads/main",
            "repository": {"full_name": "manta/repo"},
            "seq": seq,
        }
        return headers, payload

    if provider == "jira":
        headers["X-Atlassian-Webhook-Identifier"] = delivery_id
        headers["X-Atlassian-Webhook-Event"] = "jira:issue_updated"
        payload = {
            "webhookEvent": "jira:issue_updated",
            "issue": {"key": f"SCRUM-{336 + seq}"},
        }
        return headers, payload

    if provider == "slack":
        payload = {
            "type": "event_callback",
            "event_id": delivery_id,
            "event": {"type": "app_mention", "text": f"hello {seq}"},
        }
        return headers, payload

    raise ValueError(f"Unsupported provider: {provider}")


async def _query_tenant_webhook_rows(
    tenant_id: uuid.UUID,
    provider: str | None = None,
    delivery_id: str | None = None,
) -> list[dict[str, Any]]:
    """Queries `webhook_events` under the given tenant's RLS context."""
    clauses = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if provider is not None:
        clauses.append("provider = :provider")
        params["provider"] = provider
    if delivery_id is not None:
        clauses.append("provider_delivery_id = :delivery_id")
        params["delivery_id"] = delivery_id

    where_sql = " AND ".join(clauses)
    async with get_tenant_db_context(tenant_id) as session:
        res = await session.execute(
            text(
                f"SELECT id, tenant_id, integration_id, provider, provider_delivery_id, status "
                f"FROM webhook_events WHERE {where_sql} ORDER BY created_at ASC"
            ),
            params,
        )
        return [dict(r) for r in res.mappings().all()]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "jira", "slack"])
async def test_same_event_twice_per_provider(provider: str):
    """
    TC-DUP-GH-001, TC-DUP-JIRA-001, TC-DUP-SLACK-001:
    Sending the exact same webhook event twice for a provider:
    - 1st delivery -> HTTP 202, duplicate=False, 1 row in webhook_events.
    - 2nd delivery -> HTTP 202, duplicate=True, same event_id, still 1 row in webhook_events.
    """
    tenant_id = await _provision_test_tenant(f"SameTwice {provider}")
    delivery_id = f"{provider}-same-twice-{uuid.uuid4().hex[:10]}"
    headers, payload = _build_provider_webhook_request(provider, delivery_id, tenant_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res1 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload, headers=headers)
        assert res1.status_code == 202, res1.text
        body1 = res1.json()
        assert body1["duplicate"] is False
        assert body1["provider"] == provider
        assert body1["delivery_id"] == delivery_id
        assert body1["event_id"] is not None

        rows_after_first = await _query_tenant_webhook_rows(tenant_id, provider, delivery_id)
        assert len(rows_after_first) == 1

        # Second delivery of the identical event
        res2 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload, headers=headers)
        assert res2.status_code == 202, res2.text
        body2 = res2.json()
        assert body2["duplicate"] is True
        assert body2["provider"] == provider
        assert body2["delivery_id"] == delivery_id
        assert body2["event_id"] == body1["event_id"]

    rows_after_second = await _query_tenant_webhook_rows(tenant_id, provider, delivery_id)
    assert len(rows_after_second) == 1, (
        f"Expected 1 row for {provider} after duplicate redelivery, got {len(rows_after_second)}"
    )
    assert str(rows_after_second[0]["id"]) == body1["event_id"]
    assert rows_after_second[0]["status"] == "staged"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "jira", "slack"])
async def test_same_delivery_id_different_tenants_both_stored_per_provider(provider: str):
    """
    TC-DUP-GH-002, TC-DUP-JIRA-002, TC-DUP-SLACK-002:
    Sending the same delivery ID for the same provider in two different tenants (Tenant A & Tenant B):
    - Tenant A 1st delivery -> HTTP 202, duplicate=False, 1 row in Tenant A.
    - Tenant B 1st delivery -> HTTP 202, duplicate=False, 1 row in Tenant B (both stored -> 2 rows total).
    - Subsequent redeliveries within Tenant A and Tenant B return duplicate=True and keep 1 row per tenant.
    """
    tenant_a = await _provision_test_tenant(f"CrossTenA {provider}")
    tenant_b = await _provision_test_tenant(f"CrossTenB {provider}")
    shared_delivery_id = f"{provider}-shared-tenant-id-{uuid.uuid4().hex[:10]}"

    headers_a, payload_a = _build_provider_webhook_request(
        provider, shared_delivery_id, tenant_a, seq=1
    )
    headers_b, payload_b = _build_provider_webhook_request(
        provider, shared_delivery_id, tenant_b, seq=2
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Tenant A delivers shared_delivery_id
        res_a1 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload_a, headers=headers_a)
        assert res_a1.status_code == 202, res_a1.text
        data_a1 = res_a1.json()
        assert data_a1["duplicate"] is False
        assert data_a1["delivery_id"] == shared_delivery_id
        assert data_a1["event_id"] is not None

        # 2. Tenant B delivers the EXACT SAME shared_delivery_id for the SAME provider
        res_b1 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload_b, headers=headers_b)
        assert res_b1.status_code == 202, res_b1.text
        data_b1 = res_b1.json()
        assert data_b1["duplicate"] is False, (
            f"Same delivery ID in Tenant B must be stored as a new event for {provider}"
        )
        assert data_b1["delivery_id"] == shared_delivery_id
        assert data_b1["event_id"] is not None
        assert data_b1["event_id"] != data_a1["event_id"]

        # Verify expected row counts: 1 row in Tenant A, 1 row in Tenant B (2 rows total)
        rows_a = await _query_tenant_webhook_rows(tenant_a, provider, shared_delivery_id)
        rows_b = await _query_tenant_webhook_rows(tenant_b, provider, shared_delivery_id)
        assert len(rows_a) == 1, f"Expected 1 row in Tenant A for {provider}, got {len(rows_a)}"
        assert len(rows_b) == 1, f"Expected 1 row in Tenant B for {provider}, got {len(rows_b)}"
        assert len(rows_a) + len(rows_b) == 2

        # 3. Redeliver in Tenant A and Tenant B -> both detected as duplicates within their own tenant
        res_a2 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload_a, headers=headers_a)
        res_b2 = await ac.post(f"/api/v1/webhooks/{provider}", json=payload_b, headers=headers_b)
        assert res_a2.status_code == 202
        assert res_a2.json()["duplicate"] is True
        assert res_a2.json()["event_id"] == data_a1["event_id"]

        assert res_b2.status_code == 202
        assert res_b2.json()["duplicate"] is True
        assert res_b2.json()["event_id"] == data_b1["event_id"]

    # Row counts remain 1 in Tenant A and 1 in Tenant B (2 rows total)
    rows_a_final = await _query_tenant_webhook_rows(tenant_a, provider, shared_delivery_id)
    rows_b_final = await _query_tenant_webhook_rows(tenant_b, provider, shared_delivery_id)
    assert len(rows_a_final) == 1
    assert len(rows_b_final) == 1


@pytest.mark.asyncio
async def test_cross_tenant_integration_bound_same_delivery_id_both_stored():
    """
    TC-DUP-XTEN-001:
    Verifies integration-bound webhook intake (/api/v1/webhooks/integrations/{id}) when
    Tenant A and Tenant B receive the same delivery ID across GitHub, Jira, and Slack integrations:
    - Each provider stores 1 row in Tenant A and 1 row in Tenant B (2 rows per provider, 6 rows total).
    """
    tenant_a = await _provision_test_tenant("IntegCrossTenA")
    tenant_b = await _provision_test_tenant("IntegCrossTenB")

    integrations: dict[tuple[uuid.UUID, str], tuple[uuid.UUID, str]] = {}
    for tid, label in ((tenant_a, "tenA"), (tenant_b, "tenB")):
        async with get_tenant_db_context(tid) as session:
            for prov in ("github", "jira", "slack"):
                integ_id = uuid.uuid4()
                raw_secret = f"whsec_{label}_{prov}_{uuid.uuid4().hex[:8]}"
                enc_secret = encryption_service.encrypt(raw_secret)
                await session.execute(
                    text(
                        "INSERT INTO integrations ("
                        "  id, tenant_id, provider, external_account_id, webhook_secret_hash, "
                        "  is_active, created_at, updated_at"
                        ") VALUES (:id, :tenant_id, :provider, :ext_acc, :wh_secret, true, NOW(), NOW())"
                    ),
                    {
                        "id": integ_id,
                        "tenant_id": tid,
                        "provider": prov,
                        "ext_acc": f"{label}-{prov}-acc",
                        "wh_secret": enc_secret,
                    },
                )
                integrations[(tid, prov)] = (integ_id, raw_secret)

    for prov in ("github", "jira", "slack"):
        shared_del_id = f"integ-shared-{prov}-{uuid.uuid4().hex[:10]}"
        for tid in (tenant_a, tenant_b):
            integ_id, secret = integrations[(tid, prov)]
            if prov == "github":
                payload = {"action": "opened", "repository": {"full_name": "acme/repo"}}
                raw_body = json.dumps(payload).encode("utf-8")
                sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
                headers = {
                    "Content-Type": "application/json",
                    "X-GitHub-Delivery": shared_del_id,
                    "X-Hub-Signature-256": f"sha256={sig}",
                }
            elif prov == "jira":
                payload = {"webhookEvent": "jira:issue_updated", "issue": {"key": "SCRUM-336"}}
                raw_body = json.dumps(payload).encode("utf-8")
                sig = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
                headers = {
                    "Content-Type": "application/json",
                    "X-Atlassian-Webhook-Identifier": shared_del_id,
                    "X-Hub-Signature-256": f"sha256={sig}",
                }
            else:
                ts = str(int(time.time()))
                payload = {
                    "type": "event_callback",
                    "event_id": shared_del_id,
                    "event": {"type": "app_mention", "text": "check"},
                }
                raw_body = json.dumps(payload).encode("utf-8")
                base_str = f"v0:{ts}:{raw_body.decode('utf-8')}"
                sig = hmac.new(
                    secret.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                headers = {
                    "Content-Type": "application/json",
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": f"v0={sig}",
                }

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r1 = await ac.post(
                    f"/api/v1/webhooks/integrations/{integ_id}",
                    content=raw_body,
                    headers=headers,
                )
                assert r1.status_code == 202, r1.text
                assert r1.json()["duplicate"] is False

                r2 = await ac.post(
                    f"/api/v1/webhooks/integrations/{integ_id}",
                    content=raw_body,
                    headers=headers,
                )
                assert r2.status_code == 202, r2.text
                assert r2.json()["duplicate"] is True

        rows_a = await _query_tenant_webhook_rows(tenant_a, prov, shared_del_id)
        rows_b = await _query_tenant_webhook_rows(tenant_b, prov, shared_del_id)
        assert len(rows_a) == 1
        assert len(rows_b) == 1

    all_a = await _query_tenant_webhook_rows(tenant_a)
    all_b = await _query_tenant_webhook_rows(tenant_b)
    assert len(all_a) == 3
    assert len(all_b) == 3


@pytest.mark.asyncio
async def test_same_delivery_id_different_providers_both_stored():
    """
    TC-DUP-GH-003, TC-DUP-JIRA-003, TC-DUP-SLACK-003, TC-DUP-XPROV-001:
    Sending the exact same delivery_id across different providers within the same tenant:
    - Pairwise (github+jira, github+slack, jira+slack): 2 rows stored (both stored).
    - Tri-provider (github+jira+slack): 3 rows stored (all 3 stored, 1 per provider).
    - Redelivering to each provider returns duplicate=True and preserves the 3 rows.
    """
    tenant_id = await _provision_test_tenant("CrossProviderOrg")

    # 1. Pairwise combinations: each pair must store 2 rows
    pairs = [
        ("github", "jira"),
        ("github", "slack"),
        ("jira", "slack"),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for p1, p2 in pairs:
            pair_delivery_id = f"pair-{p1}-{p2}-{uuid.uuid4().hex[:8]}"
            h1, body1 = _build_provider_webhook_request(p1, pair_delivery_id, tenant_id, seq=1)
            h2, body2 = _build_provider_webhook_request(p2, pair_delivery_id, tenant_id, seq=2)

            res1 = await ac.post(f"/api/v1/webhooks/{p1}", json=body1, headers=h1)
            res2 = await ac.post(f"/api/v1/webhooks/{p2}", json=body2, headers=h2)
            assert res1.status_code == 202
            assert res1.json()["duplicate"] is False
            assert res2.status_code == 202
            assert res2.json()["duplicate"] is False
            assert res1.json()["event_id"] != res2.json()["event_id"]

            pair_rows = await _query_tenant_webhook_rows(tenant_id, delivery_id=pair_delivery_id)
            assert len(pair_rows) == 2, (
                f"Expected 2 rows for cross-provider pair ({p1}, {p2}), got {len(pair_rows)}"
            )
            assert {r["provider"] for r in pair_rows} == {p1, p2}

        # 2. All three providers sharing a single delivery_id -> 3 rows stored
        tri_delivery_id = f"tri-prov-shared-{uuid.uuid4().hex[:10]}"
        event_ids_by_provider: dict[str, str] = {}
        for idx, prov in enumerate(("github", "jira", "slack"), start=1):
            hdrs, body = _build_provider_webhook_request(prov, tri_delivery_id, tenant_id, seq=idx)
            res = await ac.post(f"/api/v1/webhooks/{prov}", json=body, headers=hdrs)
            assert res.status_code == 202
            data = res.json()
            assert data["duplicate"] is False
            assert data["delivery_id"] == tri_delivery_id
            event_ids_by_provider[prov] = data["event_id"]

        tri_rows = await _query_tenant_webhook_rows(tenant_id, delivery_id=tri_delivery_id)
        assert len(tri_rows) == 3, f"Expected 3 rows across all 3 providers, got {len(tri_rows)}"
        assert {r["provider"] for r in tri_rows} == {"github", "jira", "slack"}

        # 3. Redeliver to all 3 providers -> all 3 return duplicate=True and row count stays 3
        for idx, prov in enumerate(("github", "jira", "slack"), start=1):
            hdrs, body = _build_provider_webhook_request(prov, tri_delivery_id, tenant_id, seq=idx)
            res_dup = await ac.post(f"/api/v1/webhooks/{prov}", json=body, headers=hdrs)
            assert res_dup.status_code == 202
            dup_data = res_dup.json()
            assert dup_data["duplicate"] is True
            assert dup_data["event_id"] == event_ids_by_provider[prov]

        tri_rows_after = await _query_tenant_webhook_rows(tenant_id, delivery_id=tri_delivery_id)
        assert len(tri_rows_after) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["github", "jira", "slack"])
async def test_missing_delivery_id_scenarios_per_provider(provider: str):
    """
    TC-DUP-GH-004, TC-DUP-JIRA-004, TC-DUP-SLACK-004:
    Verifies missing explicit delivery ID behavior per provider:
    - Sub-case A (No explicit header or payload ID -> deterministic SHA-256 fallback `gen-<provider>-<hash>`):
      1. 1st delivery of payload P1 -> duplicate=False, 1 row stored.
      2. 2nd delivery of identical payload P1 -> duplicate=True, still 1 row stored.
      3. 1st delivery of distinct payload P2 -> duplicate=False, 2 rows stored total.
    - Sub-case B (Missing primary header/field, using provider-specific secondary fallback):
      1. 1st delivery -> duplicate=False, 1 row stored.
      2. 2nd delivery -> duplicate=True, still 1 row stored.
    """
    tenant_id = await _provision_test_tenant(f"MissingID {provider}")
    base_headers = {"X-Tenant-ID": str(tenant_id)}

    # Sub-case A: Completely missing delivery ID -> deterministic SHA-256 digest
    payload_p1 = {"action": "sync_alpha", "provider_hint": provider, "seq": 1}
    payload_p2 = {"action": "sync_beta", "provider_hint": provider, "seq": 2}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_p1_first = await ac.post(
            f"/api/v1/webhooks/{provider}", json=payload_p1, headers=base_headers
        )
        assert res_p1_first.status_code == 202
        d1 = res_p1_first.json()
        assert d1["duplicate"] is False
        assert d1["delivery_id"].startswith(f"gen-{provider}-")

        rows_p1_first = await _query_tenant_webhook_rows(tenant_id, provider, d1["delivery_id"])
        assert len(rows_p1_first) == 1

        # Redeliver identical payload P1 without delivery ID -> duplicate suppressed
        res_p1_second = await ac.post(
            f"/api/v1/webhooks/{provider}", json=payload_p1, headers=base_headers
        )
        assert res_p1_second.status_code == 202
        d1_dup = res_p1_second.json()
        assert d1_dup["duplicate"] is True
        assert d1_dup["delivery_id"] == d1["delivery_id"]
        assert d1_dup["event_id"] == d1["event_id"]

        rows_p1_second = await _query_tenant_webhook_rows(tenant_id, provider, d1["delivery_id"])
        assert len(rows_p1_second) == 1

        # Deliver distinct payload P2 without delivery ID -> 2nd row stored
        res_p2 = await ac.post(
            f"/api/v1/webhooks/{provider}", json=payload_p2, headers=base_headers
        )
        assert res_p2.status_code == 202
        d2 = res_p2.json()
        assert d2["duplicate"] is False
        assert d2["delivery_id"].startswith(f"gen-{provider}-")
        assert d2["delivery_id"] != d1["delivery_id"]

        rows_subcase_a = await _query_tenant_webhook_rows(tenant_id, provider)
        assert len(rows_subcase_a) == 2

        # Sub-case B: Missing primary header/field, using provider secondary fallback
        if provider == "github":
            # Missing X-GitHub-Delivery header, fallback to payload id + repository -> gh-<id>
            sec_headers = dict(base_headers)
            sec_payload: dict[str, Any] = {
                "id": 884422,
                "repository": {"full_name": "manta/core"},
            }
            expected_sec_id = "gh-884422"
        elif provider == "jira":
            # Missing X-Atlassian-Webhook-Identifier header, fallback to webhookEvent + timestamp
            sec_headers = dict(base_headers)
            sec_payload = {
                "webhookEvent": "jira:issue_created",
                "timestamp": 1730305000,
                "issue": {"key": "SCRUM-336"},
            }
            expected_sec_id = "jira-jira:issue_created-1730305000"
        else:
            # Slack missing payload event_id, fallback to X-Slack-Request-Timestamp + X-Slack-Retry-Num
            sec_headers = {
                **base_headers,
                "X-Slack-Request-Timestamp": "1730309999",
                "X-Slack-Retry-Num": "1",
            }
            sec_payload = {
                "type": "event_callback",
                "event": {"type": "reaction_added", "reaction": "shipit"},
            }
            expected_sec_id = "slack-1730309999-r1"

        res_sec_1 = await ac.post(
            f"/api/v1/webhooks/{provider}", json=sec_payload, headers=sec_headers
        )
        assert res_sec_1.status_code == 202
        assert res_sec_1.json()["duplicate"] is False
        assert res_sec_1.json()["delivery_id"] == expected_sec_id

        res_sec_2 = await ac.post(
            f"/api/v1/webhooks/{provider}", json=sec_payload, headers=sec_headers
        )
        assert res_sec_2.status_code == 202
        assert res_sec_2.json()["duplicate"] is True
        assert res_sec_2.json()["delivery_id"] == expected_sec_id

        sec_rows = await _query_tenant_webhook_rows(tenant_id, provider, expected_sec_id)
        assert len(sec_rows) == 1

        all_provider_rows = await _query_tenant_webhook_rows(tenant_id, provider)
        assert len(all_provider_rows) == 3


@pytest.mark.asyncio
async def test_missing_delivery_id_cross_provider_and_cross_tenant():
    """
    TC-DUP-MISSING-X-001:
    When an identical JSON body without any delivery ID is sent to GitHub, Jira, and Slack
    across Tenant A and Tenant B:
    - Each provider generates its own prefixed digest (`gen-github-...`, `gen-jira-...`, `gen-slack-...`).
    - Tenant A stores 3 rows (1 per provider) and Tenant B stores 3 rows (1 per provider) -> 6 rows total.
    - Repeating all 6 deliveries yields duplicate=True for all 6 and keeps the total at 6 rows.
    """
    tenant_a = await _provision_test_tenant("MissingCrossA")
    tenant_b = await _provision_test_tenant("MissingCrossB")
    shared_body = {"action": "unidentified_webhook_event", "revision": "abc1234"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        for tid in (tenant_a, tenant_b):
            hdrs = {"X-Tenant-ID": str(tid)}
            for prov in ("github", "jira", "slack"):
                r1 = await ac.post(f"/api/v1/webhooks/{prov}", json=shared_body, headers=hdrs)
                assert r1.status_code == 202
                assert r1.json()["duplicate"] is False
                assert r1.json()["delivery_id"].startswith(f"gen-{prov}-")

                r2 = await ac.post(f"/api/v1/webhooks/{prov}", json=shared_body, headers=hdrs)
                assert r2.status_code == 202
                assert r2.json()["duplicate"] is True
                assert r2.json()["event_id"] == r1.json()["event_id"]

    rows_a = await _query_tenant_webhook_rows(tenant_a)
    rows_b = await _query_tenant_webhook_rows(tenant_b)
    assert len(rows_a) == 3
    assert len(rows_b) == 3
    assert {r["provider"] for r in rows_a} == {"github", "jira", "slack"}
    assert {r["provider"] for r in rows_b} == {"github", "jira", "slack"}
