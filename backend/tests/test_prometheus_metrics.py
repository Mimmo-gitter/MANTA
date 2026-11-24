"""
TASK-302-1 (SCRUM-525 / US-49): Prometheus Metrics & Zero-PII/Secret Label Tests.

Verifies:
1. Public `GET /metrics` exposes `webhook_ack_latency_seconds` (Histogram),
   `arq_queue_depth` (Gauge), `redis_stream_consumer_lag` (Gauge), existing
   `dropped_stale_event_total`, and `prometheus-fastapi-instrumentator` HTTP metrics.
2. Accurate live updates for `webhook_ack_latency_seconds` across all webhook intake routes
   (`/api/v1/webhooks/{provider}`, `/api/v1/webhooks/integrations/{integration_id}`,
   `/api/v1/webhooks/projects/{project_id}/health`) for success and error statuses.
3. Accurate live updates for `arq_queue_depth` (`default`, `high`, `delayed`, `in_flight`)
   and `redis_stream_consumer_lag` (`tenant_stream` across multiple tenants and groups,
   including pending PEL, unread lag, missing consumer groups, `lag=None` fallback, and
   group removal reset).
4. Graceful degradation when Valkey/Redis is unavailable or slow (HTTP 200 without hanging).
5. Strict zero-PII / zero-secret leakage across all metric labels and `/metrics` output.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
from typing import Any, AsyncIterator
import uuid

from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError, ResponseError

from app.main import app
from app.observability.metrics import (
    ALLOWED_ARQ_QUEUES,
    ALLOWED_WEBHOOK_PROVIDERS,
    ALLOWED_WEBHOOK_STATUSES,
    ARQ_QUEUE_DEPTH,
    REDIS_STREAM_CONSUMER_LAG,
    WEBHOOK_ACK_LATENCY_SECONDS,
    collect_arq_queue_depths,
    collect_redis_stream_consumer_lag,
    normalize_http_status,
    normalize_stream_label,
    normalize_webhook_provider,
    observe_webhook_ack_latency,
    refresh_broker_metrics,
    sanitize_consumer_group,
    set_metrics_redis,
)
from app.projects.schemas import HealthSignalIngestResponse
from app.webhooks import router as webhooks_router_mod
from app.webhooks.service import WebhookStagingResult
from workers.retry import QUEUE_DELAYED
from workers.worker import IN_FLIGHT_PREFIX, QUEUE_DEFAULT, QUEUE_HIGH


class _FakeMetricsRedis:
    """In-memory async Redis/Valkey 7 double for ARQ queues and Redis Streams."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[tuple[str, str], dict[str, Any]] = {}
        self.custom_xinfo_groups: dict[str, list[dict[Any, Any]]] = {}
        self._seq = 0

    # --- ARQ List / ZSET operations ---
    async def rpush(self, key: str, *values: str) -> int:
        self.lists[key].extend(values)
        return len(self.lists[key])

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lmove(
        self,
        src_key: str,
        dest_key: str,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        items = self.lists.get(src_key)
        if not items:
            return None
        val = items.pop(0 if src == "LEFT" else -1)
        if dest == "LEFT":
            self.lists[dest_key].insert(0, val)
        else:
            self.lists[dest_key].append(val)
        return val

    async def lrem(self, key: str, count: int, value: str) -> int:
        items = self.lists.get(key, [])
        if value in items:
            items.remove(value)
            if not items:
                self.lists.pop(key, None)
            return 1
        return 0

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets[key].update(mapping)
        return len(mapping)

    async def zrem(self, key: str, *members: str) -> int:
        zset = self.zsets.get(key, {})
        removed = 0
        for member in members:
            if member in zset:
                del zset[member]
                removed += 1
        return removed

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    # --- Redis Streams operations ---
    async def xadd(self, name: str, fields: dict[str, str]) -> str:
        self._seq += 1
        entry_id = f"1700000000000-{self._seq}"
        self.streams.setdefault(name, []).append((entry_id, dict(fields)))
        return entry_id

    async def xlen(self, name: str) -> int:
        return len(self.streams.get(name, []))

    async def xgroup_create(
        self,
        *,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> None:
        if (name, groupname) in self.groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        if mkstream:
            self.streams.setdefault(name, [])
        cursor = 0 if id == "0" else len(self.streams.get(name, []))
        self.groups[(name, groupname)] = {
            "cursor": cursor,
            "pel": set(),
            "last_delivered_id": "0-0",
        }

    async def xreadgroup(
        self,
        *,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        del consumername
        out: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for skey in streams:
            grp = self.groups[(skey, groupname)]
            entries = self.streams.get(skey, [])
            cursor = int(grp["cursor"])
            end = len(entries) if count is None else min(len(entries), cursor + count)
            batch = entries[cursor:end]
            grp["cursor"] = end
            for eid, _ in batch:
                grp["pel"].add(eid)
                grp["last_delivered_id"] = eid
            if batch:
                out.append((skey, batch))
        return out

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        grp = self.groups.get((name, groupname))
        if grp is None:
            return 0
        acked = 0
        for eid in ids:
            if eid in grp["pel"]:
                grp["pel"].remove(eid)
                acked += 1
        return acked

    async def xinfo_groups(self, name: str) -> list[dict[Any, Any]]:
        if name in self.custom_xinfo_groups:
            return list(self.custom_xinfo_groups[name])
        total_len = len(self.streams.get(name, []))
        out: list[dict[str, Any]] = []
        for (skey, gname), grp in self.groups.items():
            if skey != name:
                continue
            cursor = int(grp["cursor"])
            out.append(
                {
                    "name": gname,
                    "consumers": 1,
                    "pending": len(grp["pel"]),
                    "last-delivered-id": grp["last_delivered_id"],
                    "entries-read": cursor,
                    "lag": max(0, total_len - cursor),
                }
            )
        return out

    async def scan_iter(self, match: str = "*") -> AsyncIterator[str]:
        prefix = match[:-1] if match.endswith("*") else match
        all_keys = set(self.lists.keys()) | set(self.streams.keys())
        for key in sorted(all_keys):
            if match.endswith("*"):
                if key.startswith(prefix):
                    yield key
            elif key == match:
                yield key


@pytest.fixture(autouse=True)
def reset_metrics_redis_override() -> Any:
    """Ensure every test isolates its metrics Redis override cleanly."""
    prev_app_redis = getattr(app.state, "metrics_redis", None)
    app.state.metrics_redis = None
    set_metrics_redis(None)
    yield
    app.state.metrics_redis = prev_app_redis
    set_metrics_redis(None)


def _hist_count(provider: str, status: str) -> float:
    val = REGISTRY.get_sample_value(
        "webhook_ack_latency_seconds_count",
        {"provider": provider, "status": status},
    )
    return float(val) if val is not None else 0.0


def _hist_sum(provider: str, status: str) -> float:
    val = REGISTRY.get_sample_value(
        "webhook_ack_latency_seconds_sum",
        {"provider": provider, "status": status},
    )
    return float(val) if val is not None else 0.0


def _gauge_arq(queue: str) -> float:
    val = REGISTRY.get_sample_value("arq_queue_depth", {"queue": queue})
    assert val is not None, f"arq_queue_depth{{queue={queue!r}}} missing"
    return float(val)


def _gauge_lag(stream: str, consumer_group: str) -> float:
    val = REGISTRY.get_sample_value(
        "redis_stream_consumer_lag",
        {"stream": stream, "consumer_group": consumer_group},
    )
    assert val is not None, (
        f"redis_stream_consumer_lag{{stream={stream!r},consumer_group={consumer_group!r}}} missing"
    )
    return float(val)


# ==============================================================================
# 1. Unit Tests: Label Sanitization & Zero-PII / Zero-Secret Guards
# ==============================================================================


class TestMetricLabelSanitizers:
    """Ensure all metric label sanitizers enforce strict allowlists and reject PII/secrets."""

    def test_normalize_webhook_provider_enforces_allowlist(self) -> None:
        assert normalize_webhook_provider("GitHub") == "github"
        assert normalize_webhook_provider("  JIRA ") == "jira"
        assert normalize_webhook_provider("slack") == "slack"
        assert normalize_webhook_provider("argocd") == "argocd"
        assert normalize_webhook_provider("generic") == "generic"
        assert normalize_webhook_provider("health") == "health"

        for bad in (
            "alice@example.com",
            "whsec_123456",
            "11111111-1111-1111-1111-111111111111",
            "gitlab",
            "",
            None,
            123,
        ):
            assert normalize_webhook_provider(bad) == "unknown"

    def test_normalize_http_status_enforces_allowlist(self) -> None:
        assert normalize_http_status(202) == "202"
        assert normalize_http_status("401") == "401"
        assert normalize_http_status(503) == "503"
        for bad in (418, "999", "not-a-status", True, None):
            assert normalize_http_status(bad) == "other"

    def test_normalize_stream_label_strips_tenant_uuid(self) -> None:
        assert (
            normalize_stream_label("tenant_stream:11111111-1111-1111-1111-111111111111")
            == "tenant_stream"
        )
        assert normalize_stream_label(b"tenant_stream:alice@corp.io") == "tenant_stream"
        assert normalize_stream_label("tenant_stream") == "tenant_stream"
        assert normalize_stream_label("arbitrary_stream:secret") == "other"
        assert normalize_stream_label(None) == "other"

    def test_sanitize_consumer_group_blocks_pii_and_secrets(self) -> None:
        assert sanitize_consumer_group("outbox-consumers") == "outbox-consumers"
        assert sanitize_consumer_group(b"dora-consumers") == "dora-consumers"
        assert sanitize_consumer_group("ws_fanout.v1") == "ws_fanout.v1"

        for unsafe in (
            "alice@example.com",
            "group-11111111-1111-1111-1111-111111111111",
            "ghp_1234567890abcdef",
            "sk_live_abc123",
            "eyJhbGciOiJSUzI1NiJ9",
            "my-secret-group",
            "consumer-token-1",
            "api_key_reader",
            "deadbeefcafebabe1234",
            "group with spaces",
            "group/with/slash",
            "a" * 60,
            "",
            None,
        ):
            assert sanitize_consumer_group(unsafe) == "other", f"Expected {unsafe!r} -> 'other'"

    def test_observe_webhook_ack_latency_clamps_negative_and_sanitizes(self) -> None:
        before = _hist_count("unknown", "other")
        prov, status, dur = observe_webhook_ack_latency(
            provider="victim@corp.com",
            status_code=418,
            duration_seconds=-0.5,
        )
        assert (prov, status, dur) == ("unknown", "other", 0.0)
        assert _hist_count("unknown", "other") == before + 1.0


# ==============================================================================
# 2. HTTP /metrics Exposition & Live Webhook ACK Latency Tests
# ==============================================================================


class TestWebhookAckLatencyMetrics:
    """Verify live webhook_ack_latency_seconds observations across all webhook endpoints."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_renders_all_three_core_metrics(self) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/metrics")

        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text

        assert "# TYPE webhook_ack_latency_seconds histogram" in body
        assert "# TYPE arq_queue_depth gauge" in body
        assert "# TYPE redis_stream_consumer_lag gauge" in body
        # Preserve existing metrics & FastAPI instrumentator HTTP metrics
        assert "# TYPE dropped_stale_event_total counter" in body
        assert "http_request_duration_seconds" in body or "http_requests_total" in body

        assert WEBHOOK_ACK_LATENCY_SECONDS is not None
        assert ARQ_QUEUE_DEPTH is not None
        assert REDIS_STREAM_CONSUMER_LAG is not None

    @pytest.mark.asyncio
    async def test_provider_webhook_endpoint_records_live_latency(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        @asynccontextmanager
        async def _fake_tenant_ctx(_tid: uuid.UUID) -> AsyncIterator[object]:
            yield object()

        async def _fake_stage(**kwargs: Any) -> WebhookStagingResult:
            return WebhookStagingResult(
                event_id=uuid.uuid4(),
                is_duplicate=False,
                provider=kwargs["provider"],
                delivery_id=kwargs["provider_delivery_id"],
            )

        async def _fake_enqueue(**_kwargs: Any) -> bool:
            return True

        monkeypatch.setattr(webhooks_router_mod, "get_tenant_db_context", _fake_tenant_ctx)
        monkeypatch.setattr(webhooks_router_mod, "stage_webhook_event_idempotent", _fake_stage)
        monkeypatch.setattr(webhooks_router_mod, "enqueue_staged_event_best_effort", _fake_enqueue)

        before_gh_202 = _hist_count("github", "202")
        before_gh_sum = _hist_sum("github", "202")
        before_unk_400 = _hist_count("unknown", "400")
        before_argo_401 = _hist_count("argocd", "401")
        before_jira_503 = _hist_count("jira", "503")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Valid GitHub webhook -> 202
            r_ok = await client.post(
                "/api/v1/webhooks/github",
                json={"action": "opened"},
                headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "del-302-1"},
            )
            assert r_ok.status_code == 202

            # 2. Unsupported provider -> 400 (labeled unknown)
            r_bad = await client.post("/api/v1/webhooks/not-a-provider", json={})
            assert r_bad.status_code == 400

            # 3. Missing ArgoCD secret -> 401 (labeled argocd)
            r_unauth = await client.post("/api/v1/webhooks/argocd", json={"app": "api"})
            assert r_unauth.status_code == 401

            # 4. Simulated DB outage on Jira webhook -> 503 (labeled jira)
            @asynccontextmanager
            async def _failing_tenant_ctx(_tid: uuid.UUID) -> AsyncIterator[object]:
                raise ConnectionError("db unreachable")
                yield  # pragma: no cover

            monkeypatch.setattr(webhooks_router_mod, "get_tenant_db_context", _failing_tenant_ctx)
            r_outage = await client.post("/api/v1/webhooks/jira", json={"webhookEvent": "test"})
            assert r_outage.status_code == 503

            # 5. Scrape /metrics and verify live histogram updates
            metrics_resp = await client.get("/metrics")

        assert metrics_resp.status_code == 200
        assert _hist_count("github", "202") == before_gh_202 + 1.0
        assert _hist_sum("github", "202") > before_gh_sum
        assert _hist_count("unknown", "400") == before_unk_400 + 1.0
        assert _hist_count("argocd", "401") == before_argo_401 + 1.0
        assert _hist_count("jira", "503") == before_jira_503 + 1.0
        assert 'webhook_ack_latency_seconds_count{provider="github",status="202"}' in (
            metrics_resp.text
        )

    @pytest.mark.asyncio
    async def test_integration_and_health_webhooks_resolve_provider_accurately(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        integ_jira = uuid.uuid4()
        integ_slack = uuid.uuid4()
        integ_missing = uuid.uuid4()
        tenant_id = uuid.uuid4()
        jira_secret = "jira-unit-test-secret"
        slack_secret = "slack-unit-test-secret"

        routes_map = {
            integ_jira: {
                "id": integ_jira,
                "tenant_id": tenant_id,
                "provider": "jira",
                "webhook_secret_hash": jira_secret,
                "is_active": True,
            },
            integ_slack: {
                "id": integ_slack,
                "tenant_id": tenant_id,
                "provider": "slack",
                "webhook_secret_hash": slack_secret,
                "is_active": True,
            },
        }

        class _FakeMappingResult:
            def __init__(self, row: dict[str, Any] | None) -> None:
                self._row = row

            def mappings(self) -> _FakeMappingResult:
                return self

            def first(self) -> dict[str, Any] | None:
                return self._row

        class _FakeSession:
            @asynccontextmanager
            async def begin(self) -> AsyncIterator[None]:
                yield

            async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
                sql = str(stmt)
                if "get_webhook_integration_route" in sql and params:
                    return _FakeMappingResult(routes_map.get(params["id"]))
                return _FakeMappingResult(None)

        @asynccontextmanager
        async def _fake_session_local() -> AsyncIterator[_FakeSession]:
            yield _FakeSession()

        async def _fake_stage(**kwargs: Any) -> WebhookStagingResult:
            return WebhookStagingResult(
                event_id=uuid.uuid4(),
                is_duplicate=False,
                provider=kwargs["provider"],
                delivery_id=kwargs["provider_delivery_id"],
            )

        async def _fake_enqueue(**_kwargs: Any) -> bool:
            return True

        async def _fake_health_handler(**kwargs: Any) -> HealthSignalIngestResponse:
            return HealthSignalIngestResponse(
                project_id=kwargs["project_id"],
                tenant_id=tenant_id,
                signal_status="PASS",
                project_health_status="HEALTHY",
                action="orphan_pass_ignored",
                noop=True,
                orphan=True,
                message="Project is already healthy; orphan PASS signal ignored.",
            )

        monkeypatch.setattr(webhooks_router_mod, "AsyncSessionLocal", _fake_session_local)
        monkeypatch.setattr(webhooks_router_mod, "stage_webhook_event_idempotent", _fake_stage)
        monkeypatch.setattr(webhooks_router_mod, "enqueue_staged_event_best_effort", _fake_enqueue)
        monkeypatch.setattr(
            webhooks_router_mod, "handle_project_health_signal_http", _fake_health_handler
        )

        before_jira_202 = _hist_count("jira", "202")
        before_slack_401 = _hist_count("slack", "401")
        before_unk_404 = _hist_count("unknown", "404")
        before_health_200 = _hist_count("health", "200")

        jira_body = json.dumps({"webhookEvent": "jira:issue_updated"}).encode("utf-8")
        jira_sig = (
            "sha256=" + hmac.new(jira_secret.encode("utf-8"), jira_body, hashlib.sha256).hexdigest()
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Valid Jira integration webhook -> 202 with resolved provider="jira"
            r_jira = await client.post(
                f"/api/v1/webhooks/integrations/{integ_jira}",
                content=jira_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": jira_sig,
                    "X-Atlassian-Webhook-Identifier": "jira-del-1",
                },
            )
            assert r_jira.status_code == 202

            # 2. Invalid Slack signature -> 401 with resolved provider="slack"
            r_slack = await client.post(
                f"/api/v1/webhooks/integrations/{integ_slack}",
                json={"type": "event_callback"},
                headers={"X-Slack-Signature": "v0=invalid"},
            )
            assert r_slack.status_code == 401

            # 3. Unknown integration ID -> 404 with provider="unknown"
            r_404 = await client.post(
                f"/api/v1/webhooks/integrations/{integ_missing}",
                json={"test": True},
            )
            assert r_404.status_code == 404

            # 4. Health signal webhook -> 200 with provider="health"
            r_health = await client.post(
                f"/api/v1/webhooks/projects/{uuid.uuid4()}/health",
                json={"status": "PASS"},
            )
            assert r_health.status_code == 200

        assert _hist_count("jira", "202") == before_jira_202 + 1.0
        assert _hist_count("slack", "401") == before_slack_401 + 1.0
        assert _hist_count("unknown", "404") == before_unk_404 + 1.0
        assert _hist_count("health", "200") == before_health_200 + 1.0


# ==============================================================================
# 3. Live ARQ Queue Depth & Redis Stream Consumer Lag Tests
# ==============================================================================


class TestBrokerLiveMetrics:
    """Verify live Valkey/Redis scraping for arq_queue_depth & redis_stream_consumer_lag."""

    @pytest.mark.asyncio
    async def test_arq_queue_depth_updates_live_on_metrics_scrape(self) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        await fake_redis.rpush(QUEUE_DEFAULT, '{"id":1}', '{"id":2}', '{"id":3}')
        await fake_redis.rpush(QUEUE_HIGH, '{"id":"h1"}', '{"id":"h2"}')
        await fake_redis.zadd(QUEUE_DELAYED, {"d1": 100.0, "d2": 105.0, "d3": 110.0, "d4": 120.0})
        await fake_redis.rpush(f"{IN_FLIGHT_PREFIX}worker-alpha-1", '{"id":"if1"}')
        await fake_redis.rpush(f"{IN_FLIGHT_PREFIX}worker-beta-2", '{"id":"if2"}')

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.get("/metrics")
            assert resp1.status_code == 200
            assert 'arq_queue_depth{queue="default"} 3.0' in resp1.text
            assert 'arq_queue_depth{queue="high"} 2.0' in resp1.text
            assert 'arq_queue_depth{queue="delayed"} 4.0' in resp1.text
            assert 'arq_queue_depth{queue="in_flight"} 2.0' in resp1.text

            # Drain queues and verify live gauges drop back to 0.0
            await fake_redis.lmove(QUEUE_DEFAULT, f"{IN_FLIGHT_PREFIX}worker-alpha-1")
            await fake_redis.lrem(f"{IN_FLIGHT_PREFIX}worker-alpha-1", 1, '{"id":1}')
            await fake_redis.lrem(f"{IN_FLIGHT_PREFIX}worker-alpha-1", 1, '{"id":"if1"}')
            await fake_redis.lrem(f"{IN_FLIGHT_PREFIX}worker-beta-2", 1, '{"id":"if2"}')
            await fake_redis.zrem(QUEUE_DELAYED, "d1", "d2", "d3", "d4")

            resp2 = await client.get("/metrics")
            assert resp2.status_code == 200
            assert _gauge_arq("default") == 2.0
            assert _gauge_arq("high") == 2.0
            assert _gauge_arq("delayed") == 0.0
            assert _gauge_arq("in_flight") == 0.0

    @pytest.mark.asyncio
    async def test_redis_stream_consumer_lag_tracks_unread_pending_and_acks_live(self) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        stream_a = f"tenant_stream:{tenant_a}"
        stream_b = f"tenant_stream:{tenant_b}"

        await fake_redis.xgroup_create(
            name=stream_a, groupname="outbox-consumers", id="0", mkstream=True
        )
        await fake_redis.xgroup_create(
            name=stream_b, groupname="outbox-consumers", id="0", mkstream=True
        )

        e1 = await fake_redis.xadd(stream_a, {"event_id": "1"})
        await fake_redis.xadd(stream_a, {"event_id": "2"})
        await fake_redis.xadd(stream_a, {"event_id": "3"})
        await fake_redis.xadd(stream_b, {"event_id": "4"})
        await fake_redis.xadd(stream_b, {"event_id": "5"})

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 5 total unread across 2 tenants -> lag = 5.0
            resp1 = await client.get("/metrics")
            assert resp1.status_code == 200
            assert _gauge_lag("tenant_stream", "outbox-consumers") == 5.0
            assert (
                'redis_stream_consumer_lag{consumer_group="outbox-consumers",stream="tenant_stream"} 5.0'
                in resp1.text
                or 'redis_stream_consumer_lag{stream="tenant_stream",consumer_group="outbox-consumers"} 5.0'
                in resp1.text
            )

            # XREADGROUP 1 entry without XACK -> unread=4, pending=1 -> total lag still 5.0
            batch = await fake_redis.xreadgroup(
                groupname="outbox-consumers",
                consumername="consumer-1",
                streams={stream_a: ">"},
                count=1,
            )
            assert len(batch) == 1
            resp2 = await client.get("/metrics")
            assert resp2.status_code == 200
            assert _gauge_lag("tenant_stream", "outbox-consumers") == 5.0

            # XACK that entry -> lag drops to 4.0
            await fake_redis.xack(stream_a, "outbox-consumers", e1)
            resp3 = await client.get("/metrics")
            assert resp3.status_code == 200
            assert _gauge_lag("tenant_stream", "outbox-consumers") == 4.0

            # Consume & XACK remaining entries on both streams -> lag drops to 0.0
            rest_a = await fake_redis.xreadgroup(
                groupname="outbox-consumers",
                consumername="consumer-1",
                streams={stream_a: ">"},
                count=10,
            )
            for _, entries in rest_a:
                await fake_redis.xack(stream_a, "outbox-consumers", *(eid for eid, _ in entries))
            rest_b = await fake_redis.xreadgroup(
                groupname="outbox-consumers",
                consumername="consumer-1",
                streams={stream_b: ">"},
                count=10,
            )
            for _, entries in rest_b:
                await fake_redis.xack(stream_b, "outbox-consumers", *(eid for eid, _ in entries))

            resp4 = await client.get("/metrics")
            assert resp4.status_code == 200
            assert _gauge_lag("tenant_stream", "outbox-consumers") == 0.0

    @pytest.mark.asyncio
    async def test_stream_without_consumer_group_and_none_lag_fallback_and_reset(self) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        # 1. Stream with entries but no consumer group yet -> falls back to XLEN
        stream_no_group = f"tenant_stream:{uuid.uuid4()}"
        await fake_redis.xadd(stream_no_group, {"event_id": "a"})
        await fake_redis.xadd(stream_no_group, {"event_id": "b"})

        # 2. Stream where Redis 7 reports lag=None (with bytes keys/values & entries-read)
        stream_none_lag = f"tenant_stream:{uuid.uuid4()}"
        for idx in range(5):
            await fake_redis.xadd(stream_none_lag, {"event_id": str(idx)})
        fake_redis.custom_xinfo_groups[stream_none_lag] = [
            {
                b"name": b"dora-consumers",
                b"pending": 1,
                b"lag": None,
                b"entries-read": 2,
                b"last-delivered-id": b"1700000000000-2",
            }
        ]

        assert await refresh_broker_metrics(redis_client=fake_redis) is True
        assert _gauge_lag("tenant_stream", "outbox-consumers") == 2.0
        # 5 total - 2 read = 3 undelivered + 1 pending = 4.0
        assert _gauge_lag("tenant_stream", "dora-consumers") == 4.0

        # 3. Remove dora-consumers group -> gauge resets cleanly to 0.0
        fake_redis.custom_xinfo_groups[stream_none_lag] = []
        fake_redis.streams.pop(stream_none_lag, None)
        assert await refresh_broker_metrics(redis_client=fake_redis) is True
        assert _gauge_lag("tenant_stream", "dora-consumers") == 0.0

    @pytest.mark.asyncio
    async def test_metrics_endpoint_degrades_gracefully_on_redis_outage_and_timeout(self) -> None:
        class _BrokenRedis:
            async def llen(self, _key: str) -> int:
                raise RedisConnectionError("redis://secretuser:supersecret@valkey:6379 refused")

            async def zcard(self, _key: str) -> int:
                raise RedisConnectionError("redis://secretuser:supersecret@valkey:6379 refused")

            async def keys(self, _pattern: str) -> list[str]:
                raise RedisConnectionError("refused")

        set_metrics_redis(_BrokenRedis())
        assert await refresh_broker_metrics() is False

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "supersecret" not in resp.text

        class _SlowRedis:
            async def llen(self, _key: str) -> int:
                await asyncio.sleep(5.0)
                return 1

            async def zcard(self, _key: str) -> int:
                await asyncio.sleep(5.0)
                return 1

            async def keys(self, _pattern: str) -> list[str]:
                await asyncio.sleep(5.0)
                return []

        assert await refresh_broker_metrics(redis_client=_SlowRedis(), timeout_seconds=0.1) is False

    @pytest.mark.asyncio
    async def test_collect_helpers_return_expected_structures(self) -> None:
        fake_redis = _FakeMetricsRedis()
        await fake_redis.rpush(QUEUE_DEFAULT, "job-1")
        depths = await collect_arq_queue_depths(fake_redis)
        assert set(depths.keys()) == set(ALLOWED_ARQ_QUEUES)
        assert depths["default"] == 1

        lag = await collect_redis_stream_consumer_lag(fake_redis)
        assert lag == {("tenant_stream", "outbox-consumers"): 0}


# ==============================================================================
# 4. Security / Privacy Verification: Zero PII & Zero Secret Leakage
# ==============================================================================


class TestZeroPiiAndSecretLeakage:
    """Adversarial checks proving zero PII, tenant UUIDs, or secrets leak into /metrics."""

    @pytest.mark.asyncio
    async def test_metrics_output_contains_zero_pii_secrets_tenant_ids_or_worker_ids(self) -> None:
        fake_redis = _FakeMetricsRedis()
        set_metrics_redis(fake_redis)

        secret_tenant_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        secret_worker_host = "prod-secret-worker-host-999"
        secret_bearer = "eyJhbGciOiJSUzI1NiJ9.SuperSecretJwtPayload.Sig987654"
        secret_webhook_token = "whsec_ultra_secret_987654321"
        secret_hmac = "deadbeefcafebabe0011223344556677"
        pii_email_1 = "victim.user@secret-corp.example"
        pii_email_2 = "john.doe@private-org.example"
        leaked_api_key = "ghp_PersonalAccessTokenSecret999"

        # Seed Redis with tenant-scoped stream, worker-scoped in-flight list, and tainted group
        tainted_stream = f"tenant_stream:{secret_tenant_id}"
        await fake_redis.rpush(f"{IN_FLIGHT_PREFIX}{secret_worker_host}", '{"job":"1"}')
        await fake_redis.xadd(tainted_stream, {"event_id": "1"})
        fake_redis.custom_xinfo_groups[tainted_stream] = [
            {"name": f"grp-{pii_email_2}", "pending": 1, "lag": 2},
            {"name": f"grp-{secret_tenant_id}", "pending": 0, "lag": 1},
            {"name": leaked_api_key, "pending": 0, "lag": 1},
        ]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Untrusted webhook provider path containing PII + secret headers + PII query string
            await client.post(
                f"/api/v1/webhooks/{pii_email_1}?email={pii_email_1}&token={leaked_api_key}",
                json={"secret": secret_webhook_token},
                headers={
                    "Authorization": f"Bearer {secret_bearer}",
                    "X-Webhook-Secret": secret_webhook_token,
                    "X-Hub-Signature-256": f"sha256={secret_hmac}",
                    "X-Tenant-ID": secret_tenant_id,
                },
            )
            # 2. Untemplated 404 path containing PII & GitHub token
            await client.get(
                f"/api/v1/untemplated/{leaked_api_key}/{pii_email_2}",
                headers={"Authorization": f"Bearer {secret_bearer}"},
            )
            # 3. Scrape /metrics
            metrics_resp = await client.get("/metrics")

        assert metrics_resp.status_code == 200
        body = metrics_resp.text

        forbidden_tokens = (
            secret_tenant_id,
            secret_worker_host,
            secret_bearer,
            "SuperSecretJwtPayload",
            secret_webhook_token,
            secret_hmac,
            pii_email_1,
            "victim.user",
            "secret-corp.example",
            pii_email_2,
            "john.doe",
            "private-org.example",
            leaked_api_key,
        )
        for token in forbidden_tokens:
            assert token not in body, f"Sensitive token {token!r} leaked into /metrics!"

        # Tainted consumer groups must collapse safely under consumer_group="other"
        assert _gauge_lag("tenant_stream", "other") == 5.0

        # Verify every sample on our 3 metrics strictly uses allowed/safe labels
        for metric_family in REGISTRY.collect():
            if metric_family.name == "webhook_ack_latency_seconds":
                for sample in metric_family.samples:
                    assert sample.labels["provider"] in ALLOWED_WEBHOOK_PROVIDERS
                    assert sample.labels["status"] in ALLOWED_WEBHOOK_STATUSES
            elif metric_family.name == "arq_queue_depth":
                for sample in metric_family.samples:
                    assert sample.labels["queue"] in ALLOWED_ARQ_QUEUES
            elif metric_family.name == "redis_stream_consumer_lag":
                for sample in metric_family.samples:
                    assert sample.labels["stream"] in {"tenant_stream", "other"}
                    assert sample.labels["consumer_group"] == sanitize_consumer_group(
                        sample.labels["consumer_group"]
                    )
