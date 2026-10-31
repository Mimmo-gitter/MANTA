"""
SCRUM-335: Comprehensive test suite for delivery identifier extraction and idempotent webhook staging.

Verifies:
1. Delivery ID extraction across GitHub, Jira, Slack, and generic providers.
2. Deterministic SHA-256 fallback when explicit delivery ID is missing.
3. Unauthenticated access on /api/v1/webhooks/* (public prefix in JWTAuthMiddleware).
4. Initial webhook delivery returns HTTP 202 Accepted with duplicate=False.
5. Redelivery of identical delivery ID returns HTTP 202 Accepted with duplicate=True (no duplicate DB rows).
6. Atomicity and conflict handling: ON CONFLICT DO NOTHING prevents unique constraint violations.
7. Integration-bound webhook intake (/api/v1/webhooks/integrations/{id}).
8. Unsupported providers return HTTP 400.
"""

import hashlib
import hmac
import json
import uuid
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text

from app.db.session import async_engine, get_tenant_db_context
from app.integrations.crypto import encryption_service
from app.main import app
from app.webhooks.delivery import extract_delivery_id


@pytest.fixture(autouse=True)
async def cleanup_engine():
    """Teardown database engine connections per test event loop."""
    yield
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
