"""Automated Test Suite: Durable Webhook Staging & PostgreSQL Outage Resilience (SCRUM-251 / US-15).

Covers all staging and failure invariants end-to-end:
1. Valid signed webhooks (GitHub, Jira, Slack) are durably committed to PostgreSQL
   `webhook_events` BEFORE HTTP 202 Accepted is emitted (verified via SQLAlchemy
   `after_commit` monotonic timestamps AND an independent READ COMMITTED query
   executed inside an ASGI `http.response.start` spy).
2. Invalid signatures (tampered payload, wrong HMAC secret, missing signature header,
   expired Slack replay timestamp) return HTTP 401 (never 202) and store 0 rows.
3. Simulated PostgreSQL outages in the test environment:
   - Unreachable TCP database endpoint (`ConnectionRefusedError` / `OSError`).
   - Live PostgreSQL mid-transaction write failure (`SET LOCAL transaction_read_only = on`
     causing `INSERT INTO webhook_events` to fail after route lookup & HMAC verification pass).
   - `asyncpg` server shutdown / pool exhaustion / interface exceptions (`CannotConnectNowError`,
     `TooManyConnectionsError`, `InterfaceError`, `TimeoutError`).
   - Concurrent burst of 20 signed webhooks during an active outage.
   Every outage path proves HTTP 503 with `Retry-After: 30` is returned, NO HTTP 202 is
   ever emitted, 0 rows are stored in `webhook_events`, and post-recovery replay succeeds.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import json
import socket
import time
from typing import Any
import uuid

import asyncpg
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import app.db.session as db_session
from app.integrations.crypto import encryption_service
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
import app.webhooks.router as webhook_router


@pytest_asyncio.fixture(autouse=True)
async def cleanup_engine():
    """Reset registration rate limiter and dispose shared async engine per test."""
    registration_rate_limiter.reset()
    yield
    registration_rate_limiter.reset()
    await db_session.async_engine.dispose()


def _sign_github_or_jira(secret: str, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _sign_slack(secret: str, timestamp: int | str, raw_body: bytes) -> str:
    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}"
    mac = hmac.new(secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"v0={mac}"


def _build_signed_request(
    provider: str,
    secret: str,
    delivery_id: str,
    seq: int = 1,
    timestamp_override: int | None = None,
) -> tuple[bytes, dict[str, str], dict[str, Any]]:
    """Constructs a signed JSON webhook payload and provider-authentic headers."""
    if provider == "github":
        payload = {
            "action": "opened",
            "number": seq,
            "repository": {"full_name": "manta-org/platform"},
            "sender": {"login": "dev-actor"},
        }
        raw_body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery_id,
            "X-Hub-Signature-256": _sign_github_or_jira(secret, raw_body),
        }
        return raw_body, headers, payload

    if provider == "jira":
        payload = {
            "webhookEvent": "jira:issue_updated",
            "issue": {"key": f"SCRUM-{seq}", "fields": {"summary": "Durable staging check"}},
        }
        raw_body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Atlassian-Webhook-Event": "jira:issue_updated",
            "X-Atlassian-Webhook-Identifier": delivery_id,
            "X-Hub-Signature-256": _sign_github_or_jira(secret, raw_body),
        }
        return raw_body, headers, payload

    if provider == "slack":
        ts = timestamp_override if timestamp_override is not None else int(time.time())
        payload = {
            "type": "event_callback",
            "event_id": delivery_id,
            "event": {"type": "app_mention", "text": f"deploy check #{seq}"},
        }
        raw_body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": _sign_slack(secret, ts, raw_body),
        }
        return raw_body, headers, payload

    raise ValueError(f"Unsupported test provider: {provider}")


class ASGIResponseSpy:
    """Wraps an ASGI app to record status codes and invoke an async hook BEFORE response emission."""

    def __init__(
        self,
        inner_app: Any,
        on_response_start: Callable[[int, int], Any] | None = None,
    ) -> None:
        self.inner_app = inner_app
        self.on_response_start = on_response_start
        self.emitted_statuses: list[int] = []
        self.response_start_timestamps_ns: list[int] = []

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.inner_app(scope, receive, send)
            return

        async def wrapped_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                ts_ns = time.perf_counter_ns()
                status_code = int(message["status"])
                self.emitted_statuses.append(status_code)
                self.response_start_timestamps_ns.append(ts_ns)
                if self.on_response_start is not None:
                    await self.on_response_start(status_code, ts_ns)
            await send(message)

        await self.inner_app(scope, receive, wrapped_send)


async def _register_tenant_and_integrations() -> tuple[uuid.UUID, dict[str, tuple[uuid.UUID, str]]]:
    """Creates an isolated tenant and active GitHub, Jira, and Slack integrations."""
    suffix = uuid.uuid4().hex[:10]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reg = await client.post(
            "/tenants/register",
            json={
                "name": f"Staging Test Org {suffix}",
                "slug": f"staging-test-{suffix}",
                "admin_email": f"ops@{suffix}.example.com",
                "admin_name": f"Ops {suffix}",
            },
        )
        assert reg.status_code == 201, f"Tenant registration failed: {reg.text}"
        tenant_id = uuid.UUID(reg.json()["tenant_id"])

    integrations: dict[str, tuple[uuid.UUID, str]] = {}
    async with db_session.get_tenant_db_context(tenant_id) as session:
        for provider in ("github", "jira", "slack"):
            integration_id = uuid.uuid4()
            raw_secret = f"{provider}_whsec_{suffix}_secret_key_256"
            encrypted_secret = encryption_service.encrypt(raw_secret)
            await session.execute(
                text(
                    "INSERT INTO integrations ("
                    "  id, tenant_id, provider, external_account_id, webhook_secret_hash, "
                    "  is_active, created_at, updated_at"
                    ") VALUES (:id, :tenant_id, :provider, :ext_id, :secret, true, NOW(), NOW())"
                ),
                {
                    "id": integration_id,
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "ext_id": f"{provider}-acct-{suffix}",
                    "secret": encrypted_secret,
                },
            )
            integrations[provider] = (integration_id, raw_secret)

    return tenant_id, integrations


async def _fetch_tenant_webhook_events(
    tenant_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[dict[str, Any]]:
    """Queries `webhook_events` under RLS tenant context via an independent session."""
    factory = session_factory or db_session.AsyncSessionLocal
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            res = await session.execute(
                text(
                    "SELECT id, tenant_id, integration_id, provider, provider_delivery_id, "
                    "       event_type, status, payload "
                    "FROM webhook_events "
                    "WHERE tenant_id = :tenant_id "
                    "ORDER BY received_at ASC"
                ),
                {"tenant_id": tenant_id},
            )
            return [dict(row) for row in res.mappings().all()]


def _allocate_closed_local_port() -> int:
    """Binds and immediately closes an ephemeral localhost TCP port so connections are refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def _simulate_unreachable_postgres_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Redirects app database sessions to a real SQLAlchemy async engine on a closed TCP port.

    Yields the healthy original session factory so assertions can inspect live PostgreSQL
    even while the simulated outage is active.
    """
    healthy_factory = db_session.AsyncSessionLocal
    closed_port = _allocate_closed_local_port()
    dead_dsn = (
        f"postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod"
        f"@127.0.0.1:{closed_port}/manta_db"
    )
    outage_engine = create_async_engine(
        dead_dsn,
        connect_args={
            "timeout": 1,
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=10,
        pool_timeout=2,
    )
    outage_factory = async_sessionmaker(
        bind=outage_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(db_session, "AsyncSessionLocal", outage_factory)
    monkeypatch.setattr(webhook_router, "AsyncSessionLocal", outage_factory)
    try:
        yield healthy_factory
    finally:
        monkeypatch.setattr(db_session, "AsyncSessionLocal", healthy_factory)
        monkeypatch.setattr(webhook_router, "AsyncSessionLocal", healthy_factory)
        await outage_engine.dispose()


@asynccontextmanager
async def _simulate_readonly_storage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Forces real PostgreSQL transactions into `transaction_read_only = on` mode.

    Integration lookup (`get_webhook_integration_route`), AES-GCM decryption, HMAC-SHA256
    signature verification, and `set_config` execute normally against live PostgreSQL, while
    `INSERT INTO webhook_events` fails in PostgreSQL with `ReadOnlySqlTransactionError`.
    """
    healthy_factory = db_session.AsyncSessionLocal

    class _ReadOnlyBeginContext:
        def __init__(self, inner_session: AsyncSession) -> None:
            self._inner_session = inner_session
            self._tx_ctx: Any = None

        async def __aenter__(self) -> Any:
            self._tx_ctx = self._inner_session.begin()
            tx = await self._tx_ctx.__aenter__()
            await self._inner_session.execute(text("SET LOCAL transaction_read_only = on"))
            return tx

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
            return await self._tx_ctx.__aexit__(exc_type, exc, tb)

    class _ReadOnlySessionProxy:
        def __init__(self, inner_session: AsyncSession) -> None:
            self._inner_session = inner_session

        def begin(self) -> _ReadOnlyBeginContext:
            return _ReadOnlyBeginContext(self._inner_session)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner_session, name)

    @asynccontextmanager
    async def readonly_session_factory() -> AsyncIterator[Any]:
        async with healthy_factory() as real_session:
            yield _ReadOnlySessionProxy(real_session)

    monkeypatch.setattr(db_session, "AsyncSessionLocal", readonly_session_factory)
    monkeypatch.setattr(webhook_router, "AsyncSessionLocal", readonly_session_factory)
    try:
        yield healthy_factory
    finally:
        monkeypatch.setattr(db_session, "AsyncSessionLocal", healthy_factory)
        monkeypatch.setattr(webhook_router, "AsyncSessionLocal", healthy_factory)


@pytest.mark.asyncio
async def test_valid_webhook_is_durably_stored_before_202_acknowledgment():
    """Scope 1: Valid signed event is durably committed in PostgreSQL BEFORE HTTP 202 is sent."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    healthy_factory = db_session.AsyncSessionLocal

    commit_timestamps_ns: list[int] = []

    def _on_after_commit(_session: Session) -> None:
        commit_timestamps_ns.append(time.perf_counter_ns())

    event.listen(Session, "after_commit", _on_after_commit)
    try:
        # 1. Verify integration-bound intake (`/api/v1/webhooks/integrations/{integration_id}`)
        for idx, (provider, (integration_id, secret)) in enumerate(integrations.items(), start=1):
            delivery_id = f"valid-integ-{provider}-{uuid.uuid4().hex[:10]}"
            raw_body, headers, expected_payload = _build_signed_request(
                provider=provider,
                secret=secret,
                delivery_id=delivery_id,
                seq=idx,
            )

            pre_response_snapshots: list[list[dict[str, Any]]] = []
            router_commit_ns_box: list[int] = []
            commit_count_before = len(commit_timestamps_ns)

            async def _verify_committed_before_202(
                status_code: int,
                resp_ts_ns: int,
                expected_del_id: str = delivery_id,
                expected_prev_commits: int = commit_count_before,
            ) -> None:
                assert status_code == 202
                # Capture the router's DB commit timestamp BEFORE opening our verification session
                assert len(commit_timestamps_ns) > expected_prev_commits
                router_commit_ns = commit_timestamps_ns[-1]
                assert router_commit_ns < resp_ts_ns, (
                    f"Expected DB commit ({router_commit_ns}) < HTTP 202 start ({resp_ts_ns})"
                )
                router_commit_ns_box.append(router_commit_ns)
                # Query via an independent READ COMMITTED connection BEFORE 202 reaches client
                rows = await _fetch_tenant_webhook_events(tenant_id, healthy_factory)
                matching = [r for r in rows if r["provider_delivery_id"] == expected_del_id]
                pre_response_snapshots.append(matching)

            spy_app = ASGIResponseSpy(app, on_response_start=_verify_committed_before_202)
            async with AsyncClient(
                transport=ASGITransport(app=spy_app), base_url="http://test"
            ) as client:
                res = await client.post(
                    f"/api/v1/webhooks/integrations/{integration_id}",
                    content=raw_body,
                    headers=headers,
                )

            assert res.status_code == 202, res.text
            body = res.json()
            assert body["status"] == "accepted"
            assert body["duplicate"] is False
            assert body["provider"] == provider
            assert body["delivery_id"] == delivery_id

            # Proof A: Monotonic timestamp ordering — DB commit finished strictly before HTTP 202 started
            assert len(router_commit_ns_box) == 1
            assert router_commit_ns_box[0] < spy_app.response_start_timestamps_ns[-1]

            # Proof B: Independent READ COMMITTED session already saw the staged row before 202 emitted
            assert len(pre_response_snapshots) == 1
            assert len(pre_response_snapshots[0]) == 1
            staged_row = pre_response_snapshots[0][0]
            assert str(staged_row["id"]) == body["event_id"]
            assert staged_row["tenant_id"] == tenant_id
            assert staged_row["integration_id"] == integration_id
            assert staged_row["provider"] == provider
            assert staged_row["status"] == "staged"
            assert staged_row["payload"] == expected_payload

        # 2. Verify provider-level signed intake (`/api/v1/webhooks/{provider}`)
        prov_delivery_id = f"valid-provider-gh-{uuid.uuid4().hex[:10]}"
        prov_secret = "provider_level_hmac_secret_987654"
        raw_body, headers, expected_payload = _build_signed_request(
            provider="github",
            secret=prov_secret,
            delivery_id=prov_delivery_id,
            seq=99,
        )
        headers["X-Tenant-ID"] = str(tenant_id)
        headers["X-Webhook-Secret"] = prov_secret

        provider_pre_response_rows: list[dict[str, Any]] = []
        provider_commit_ns_box: list[int] = []
        commit_count_before = len(commit_timestamps_ns)

        async def _verify_provider_committed_before_202(status_code: int, resp_ts_ns: int) -> None:
            assert status_code == 202
            assert len(commit_timestamps_ns) > commit_count_before
            router_commit_ns = commit_timestamps_ns[-1]
            assert router_commit_ns < resp_ts_ns
            provider_commit_ns_box.append(router_commit_ns)
            rows = await _fetch_tenant_webhook_events(tenant_id, healthy_factory)
            provider_pre_response_rows.extend(
                r for r in rows if r["provider_delivery_id"] == prov_delivery_id
            )

        spy_provider = ASGIResponseSpy(app, on_response_start=_verify_provider_committed_before_202)
        async with AsyncClient(
            transport=ASGITransport(app=spy_provider), base_url="http://test"
        ) as client:
            res_prov = await client.post(
                "/api/v1/webhooks/github",
                content=raw_body,
                headers=headers,
            )

        assert res_prov.status_code == 202, res_prov.text
        assert len(provider_commit_ns_box) == 1
        assert provider_commit_ns_box[0] < spy_provider.response_start_timestamps_ns[-1]
        assert len(provider_pre_response_rows) == 1
        assert provider_pre_response_rows[0]["status"] == "staged"
        assert provider_pre_response_rows[0]["payload"] == expected_payload
    finally:
        event.remove(Session, "after_commit", _on_after_commit)


@pytest.mark.asyncio
async def test_invalid_signature_returns_401_and_stores_nothing():
    """Scope 1: Invalid/missing/expired signatures return HTTP 401 and persist 0 rows."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    spy_app = ASGIResponseSpy(app)

    gh_id, gh_secret = integrations["github"]
    jira_id, _jira_secret = integrations["jira"]
    slack_id, slack_secret = integrations["slack"]

    gh_body, gh_headers, _ = _build_signed_request("github", gh_secret, f"bad-gh-{uuid.uuid4()}", 1)
    tampered_gh_body = json.dumps({"action": "tampered", "malicious": True}).encode("utf-8")

    jira_body, jira_headers, _ = _build_signed_request(
        "jira", "wrong_jira_secret_value", f"bad-jira-{uuid.uuid4()}", 2
    )

    expired_ts = int(time.time()) - 600  # 10 minutes old (> 300s replay window)
    slack_expired_body, slack_expired_headers, _ = _build_signed_request(
        "slack",
        slack_secret,
        f"expired-slack-{uuid.uuid4()}",
        3,
        timestamp_override=expired_ts,
    )

    invalid_cases: list[tuple[str, str, bytes, dict[str, str]]] = [
        # Integration-bound endpoint cases
        (
            "github-tampered-body",
            f"/api/v1/webhooks/integrations/{gh_id}",
            tampered_gh_body,
            gh_headers,
        ),
        (
            "github-missing-signature",
            f"/api/v1/webhooks/integrations/{gh_id}",
            gh_body,
            {k: v for k, v in gh_headers.items() if k != "X-Hub-Signature-256"},
        ),
        (
            "jira-wrong-secret",
            f"/api/v1/webhooks/integrations/{jira_id}",
            jira_body,
            jira_headers,
        ),
        (
            "slack-expired-replay-timestamp",
            f"/api/v1/webhooks/integrations/{slack_id}",
            slack_expired_body,
            slack_expired_headers,
        ),
        # Provider-level endpoint cases with X-Webhook-Secret
        (
            "provider-github-tampered",
            "/api/v1/webhooks/github",
            tampered_gh_body,
            {
                **gh_headers,
                "X-Tenant-ID": str(tenant_id),
                "X-Webhook-Secret": gh_secret,
            },
        ),
        (
            "provider-slack-wrong-secret",
            "/api/v1/webhooks/slack",
            slack_expired_body,
            {
                "Content-Type": "application/json",
                "X-Tenant-ID": str(tenant_id),
                "X-Webhook-Secret": slack_secret,
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=0000000000000000000000000000000000000000000000000000000000000000",
            },
        ),
    ]

    async with AsyncClient(transport=ASGITransport(app=spy_app), base_url="http://test") as client:
        for label, url, content, headers in invalid_cases:
            res = await client.post(url, content=content, headers=headers)
            assert res.status_code == 401, (
                f"Case {label} expected 401, got {res.status_code}: {res.text}"
            )
            assert res.status_code != 202

    assert 202 not in spy_app.emitted_statuses
    assert all(code == 401 for code in spy_app.emitted_statuses)

    # Verify PostgreSQL `webhook_events` stored NOTHING for this tenant
    persisted_rows = await _fetch_tenant_webhook_events(tenant_id)
    assert persisted_rows == [], (
        f"Expected 0 stored rows for invalid signatures, got {persisted_rows}"
    )


@pytest.mark.asyncio
async def test_simulated_network_outage_unreachable_postgres_returns_503_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Scopes 1 & 2, AC 2: Real TCP database outage in test environment -> 503 (never 202) & 0 rows."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    gh_id, gh_secret = integrations["github"]
    slack_id, slack_secret = integrations["slack"]

    gh_delivery = f"outage-net-gh-{uuid.uuid4().hex[:10]}"
    gh_body, gh_headers, gh_payload = _build_signed_request("github", gh_secret, gh_delivery, 10)

    slack_delivery = f"outage-net-slack-{uuid.uuid4().hex[:10]}"
    slack_body, slack_headers, _ = _build_signed_request("slack", slack_secret, slack_delivery, 11)

    prov_delivery = f"outage-net-prov-{uuid.uuid4().hex[:10]}"
    prov_body, prov_headers, _ = _build_signed_request("github", gh_secret, prov_delivery, 12)
    prov_headers["X-Tenant-ID"] = str(tenant_id)
    prov_headers["X-Webhook-Secret"] = gh_secret

    spy_outage = ASGIResponseSpy(app)
    async with _simulate_unreachable_postgres_outage(monkeypatch) as healthy_factory:
        async with AsyncClient(
            transport=ASGITransport(app=spy_outage), base_url="http://test"
        ) as client:
            res_gh = await client.post(
                f"/api/v1/webhooks/integrations/{gh_id}",
                content=gh_body,
                headers=gh_headers,
            )
            res_slack = await client.post(
                f"/api/v1/webhooks/integrations/{slack_id}",
                content=slack_body,
                headers=slack_headers,
            )
            res_prov = await client.post(
                "/api/v1/webhooks/github",
                content=prov_body,
                headers=prov_headers,
            )

        for res in (res_gh, res_slack, res_prov):
            assert res.status_code == 503, f"Expected 503 during DB outage, got {res.status_code}"
            assert res.status_code != 202
            assert res.headers.get("Retry-After") == "30"
            assert "Database storage service unavailable" in res.json()["detail"]

        # AC 2: Prove NO 202 was ever emitted by ASGI during outage and 0 rows exist in PostgreSQL
        assert 202 not in spy_outage.emitted_statuses
        assert spy_outage.emitted_statuses == [503, 503, 503]
        during_outage_rows = await _fetch_tenant_webhook_events(tenant_id, healthy_factory)
        assert during_outage_rows == []

    # Post-outage recovery proof: replaying the exact signed GitHub webhook now stages cleanly (202)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res_recovered = await client.post(
            f"/api/v1/webhooks/integrations/{gh_id}",
            content=gh_body,
            headers=gh_headers,
        )
    assert res_recovered.status_code == 202, res_recovered.text
    recovered_json = res_recovered.json()
    assert recovered_json["duplicate"] is False
    assert recovered_json["delivery_id"] == gh_delivery

    after_recovery_rows = await _fetch_tenant_webhook_events(tenant_id)
    assert len(after_recovery_rows) == 1
    assert after_recovery_rows[0]["provider_delivery_id"] == gh_delivery
    assert after_recovery_rows[0]["payload"] == gh_payload
    assert after_recovery_rows[0]["status"] == "staged"


@pytest.mark.asyncio
async def test_simulated_mid_transaction_storage_failure_proves_no_202_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """AC 2: Route lookup & HMAC verification pass on live PostgreSQL, then storage INSERT fails."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    jira_id, jira_secret = integrations["jira"]

    jira_delivery = f"outage-ro-jira-{uuid.uuid4().hex[:10]}"
    jira_body, jira_headers, _ = _build_signed_request("jira", jira_secret, jira_delivery, 21)

    prov_delivery = f"outage-ro-prov-{uuid.uuid4().hex[:10]}"
    prov_body, prov_headers, _ = _build_signed_request("jira", jira_secret, prov_delivery, 22)
    prov_headers["X-Tenant-ID"] = str(tenant_id)
    prov_headers["X-Webhook-Secret"] = jira_secret

    spy_ro = ASGIResponseSpy(app)
    async with _simulate_readonly_storage_failure(monkeypatch) as healthy_factory:
        async with AsyncClient(
            transport=ASGITransport(app=spy_ro), base_url="http://test"
        ) as client:
            # 1. Valid signature -> reaches PostgreSQL INSERT -> fails with ReadOnlySqlTransactionError -> 503
            res_integ = await client.post(
                f"/api/v1/webhooks/integrations/{jira_id}",
                content=jira_body,
                headers=jira_headers,
            )
            res_prov = await client.post(
                "/api/v1/webhooks/jira",
                content=prov_body,
                headers=prov_headers,
            )

            # 2. Confirm route lookup + HMAC verification genuinely ran against live PostgreSQL
            # (an invalid signature still fails at verification with 401 before reaching INSERT)
            bad_headers = {**jira_headers, "X-Hub-Signature-256": "sha256=" + ("0" * 64)}
            res_bad_sig = await client.post(
                f"/api/v1/webhooks/integrations/{jira_id}",
                content=jira_body,
                headers=bad_headers,
            )

        assert res_integ.status_code == 503
        assert res_integ.status_code != 202
        assert res_integ.headers.get("Retry-After") == "30"

        assert res_prov.status_code == 503
        assert res_prov.status_code != 202
        assert res_prov.headers.get("Retry-After") == "30"

        assert res_bad_sig.status_code == 401

        assert 202 not in spy_ro.emitted_statuses
        assert spy_ro.emitted_statuses == [503, 503, 401]
        assert await _fetch_tenant_webhook_events(tenant_id, healthy_factory) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "simulated_exc",
    [
        asyncpg.CannotConnectNowError("the database system is shutting down"),
        asyncpg.TooManyConnectionsError("sorry, too many clients already"),
        asyncpg.InterfaceError("connection is closed"),
        TimeoutError("connection pool checkout timed out"),
    ],
)
async def test_asyncpg_connection_shutdown_errors_return_503_never_202(
    monkeypatch: pytest.MonkeyPatch,
    simulated_exc: Exception,
):
    """Hardening check: connection-phase asyncpg/pool exceptions return 503 (never 202 or 500)."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    gh_id, gh_secret = integrations["github"]
    healthy_factory = db_session.AsyncSessionLocal

    @asynccontextmanager
    async def failing_session_factory() -> AsyncIterator[AsyncSession]:
        raise simulated_exc
        yield  # pragma: no cover

    monkeypatch.setattr(db_session, "AsyncSessionLocal", failing_session_factory)
    monkeypatch.setattr(webhook_router, "AsyncSessionLocal", failing_session_factory)

    spy = ASGIResponseSpy(app)
    try:
        raw_body, headers, _ = _build_signed_request(
            "github", gh_secret, f"shutdown-{uuid.uuid4().hex[:8]}", 30
        )
        prov_headers = {
            **headers,
            "X-Tenant-ID": str(tenant_id),
            "X-Webhook-Secret": gh_secret,
        }
        async with AsyncClient(transport=ASGITransport(app=spy), base_url="http://test") as client:
            res_integ = await client.post(
                f"/api/v1/webhooks/integrations/{gh_id}",
                content=raw_body,
                headers=headers,
            )
            res_prov = await client.post(
                "/api/v1/webhooks/github",
                content=raw_body,
                headers=prov_headers,
            )
    finally:
        monkeypatch.setattr(db_session, "AsyncSessionLocal", healthy_factory)
        monkeypatch.setattr(webhook_router, "AsyncSessionLocal", healthy_factory)

    for res in (res_integ, res_prov):
        assert res.status_code == 503
        assert res.status_code != 202
        assert res.headers.get("Retry-After") == "30"

    assert 202 not in spy.emitted_statuses
    assert spy.emitted_statuses == [503, 503]
    assert await _fetch_tenant_webhook_events(tenant_id, healthy_factory) == []


@pytest.mark.asyncio
async def test_concurrent_signed_webhooks_during_outage_never_return_202(
    monkeypatch: pytest.MonkeyPatch,
):
    """Burst verification: 20 concurrent signed webhooks during DB outage yield 0 HTTP 202s & 0 rows."""
    tenant_id, integrations = await _register_tenant_and_integrations()
    providers = ("github", "jira", "slack")

    prepared_requests: list[tuple[str, bytes, dict[str, str]]] = []
    for idx in range(20):
        provider = providers[idx % len(providers)]
        integration_id, secret = integrations[provider]
        delivery_id = f"burst-{provider}-{idx:02d}-{uuid.uuid4().hex[:8]}"
        raw_body, headers, _ = _build_signed_request(provider, secret, delivery_id, seq=100 + idx)
        prepared_requests.append(
            (f"/api/v1/webhooks/integrations/{integration_id}", raw_body, headers)
        )

    spy_burst = ASGIResponseSpy(app)
    async with _simulate_unreachable_postgres_outage(monkeypatch) as healthy_factory:
        async with AsyncClient(
            transport=ASGITransport(app=spy_burst), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.post(url, content=body, headers=hdrs)
                    for url, body, hdrs in prepared_requests
                )
            )

        assert len(responses) == 20
        assert all(r.status_code == 503 for r in responses)
        assert all(r.status_code != 202 for r in responses)
        assert all(r.headers.get("Retry-After") == "30" for r in responses)
        assert 202 not in spy_burst.emitted_statuses
        assert spy_burst.emitted_statuses == [503] * 20
        assert await _fetch_tenant_webhook_events(tenant_id, healthy_factory) == []

    # Replay all 20 after database recovers -> all 20 return 202 and persist 20 rows
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        replay_responses = await asyncio.gather(
            *(client.post(url, content=body, headers=hdrs) for url, body, hdrs in prepared_requests)
        )
    assert all(r.status_code == 202 for r in replay_responses)
    staged_after_replay = await _fetch_tenant_webhook_events(tenant_id)
    assert len(staged_after_replay) == 20
