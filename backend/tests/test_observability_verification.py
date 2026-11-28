"""
TASK-302-3 (SCRUM-527 / US-49): Pytest Observability Verification Suite.

Verifies:
1. `/metrics` Prometheus series exposition and live updates for:
   - `webhook_ack_latency_seconds` (Histogram: `_bucket`, `_count`, `_sum`)
   - `arq_queue_depth` (Gauge: `default`, `high`, `delayed`, `in_flight`)
   - `redis_stream_consumer_lag` (Gauge: `stream="tenant_stream"`, `consumer_group`)
2. Single-`trace_id` end-to-end propagation across all pipeline hops:
   FastAPI webhook intake -> ARQ worker dispatch -> transactional outbox staging ->
   OutboxRelayWorker XADD -> OutboxStreamConsumer -> WebSocketGatewayManager
   (live broadcast & replay), plus isolation between concurrent traced pipelines.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import re
from typing import Any, AsyncIterator
import uuid
from unittest import mock

from httpx import ASGITransport, AsyncClient
import pytest

from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import OutboxEvent
from app.main import app
from app.observability.metrics import set_metrics_redis
from app.observability.tracing import (
    get_current_trace_id_hex,
    temporary_in_memory_span_exporter,
)
from app.outbox.consumer import OutboxStreamConsumer
from app.outbox.relay import OutboxRelayWorker, build_stream_key
from app.outbox.service import record_outbox_event
from app.webhooks import router as webhooks_router_mod
from app.webhooks.enqueue import build_webhook_envelope
from app.webhooks.service import WebhookStagingResult
from tests.test_prometheus_metrics import _FakeMetricsRedis
from tests.test_websocket_auth_and_fanout import InMemoryStreamsRedis, _FakeWebSocket
from workers.retry import QUEUE_DELAYED
from workers.worker import IN_FLIGHT_PREFIX, QUEUE_DEFAULT, QUEUE_HIGH, dispatch_worker_job


_PROM_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+0-9.eE]+|NaN|[+-]Inf)$"
)
_LABEL_PAIR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')


def _parse_prometheus_samples(body: str, metric_name: str) -> list[tuple[dict[str, str], float]]:
    """Parse matching metric lines from Prometheus text exposition output."""
    samples: list[tuple[dict[str, str], float]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PROM_LINE_RE.match(line)
        if not match or match.group("name") != metric_name:
            continue
        raw_labels = match.group("labels") or ""
        labels = dict(_LABEL_PAIR_RE.findall(raw_labels))
        samples.append((labels, float(match.group("value"))))
    return samples


def _find_metric_value(
    body: str,
    metric_name: str,
    expected_labels: dict[str, str],
) -> float | None:
    """Return the sample value matching `metric_name` and all `expected_labels`."""
    for labels, value in _parse_prometheus_samples(body, metric_name):
        if all(labels.get(k) == v for k, v in expected_labels.items()):
            return value
    return None


class _FakeDbScalarResult:
    def __init__(self, value: Any = None, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows or []

    def scalars(self) -> "_FakeDbScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._value


class TestPrometheusMetricsSeriesVerification:
    """Verify `/metrics` Prometheus histogram & gauge series end-to-end."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_exposes_prometheus_series_and_buckets(self) -> None:
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

        assert _parse_prometheus_samples(body, "webhook_ack_latency_seconds_bucket")
        assert _parse_prometheus_samples(body, "webhook_ack_latency_seconds_count")
        assert _parse_prometheus_samples(body, "webhook_ack_latency_seconds_sum")
        assert _parse_prometheus_samples(body, "arq_queue_depth")
        assert _parse_prometheus_samples(body, "redis_stream_consumer_lag")

    @pytest.mark.asyncio
    async def test_metrics_series_reflect_live_webhook_arq_and_stream_lifecycle(
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
                event_id=generate_uuidv7(),
                is_duplicate=False,
                provider=kwargs["provider"],
                delivery_id=kwargs["provider_delivery_id"],
            )

        async def _fake_enqueue(**_kwargs: Any) -> bool:
            return True

        monkeypatch.setattr(webhooks_router_mod, "get_tenant_db_context", _fake_tenant_ctx)
        monkeypatch.setattr(webhooks_router_mod, "stage_webhook_event_idempotent", _fake_stage)
        monkeypatch.setattr(webhooks_router_mod, "enqueue_staged_event_best_effort", _fake_enqueue)

        tenant_id = uuid.uuid4()
        stream_key = build_stream_key(tenant_id)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            baseline = await client.get("/metrics")
            assert baseline.status_code == 200
            before_count = (
                _find_metric_value(
                    baseline.text,
                    "webhook_ack_latency_seconds_count",
                    {"provider": "github", "status": "202"},
                )
                or 0.0
            )
            before_sum = (
                _find_metric_value(
                    baseline.text,
                    "webhook_ack_latency_seconds_sum",
                    {"provider": "github", "status": "202"},
                )
                or 0.0
            )

            # 1. Trigger webhook intake -> histogram count, +Inf bucket, and sum update
            wh_resp = await client.post(
                "/api/v1/webhooks/github",
                json={"action": "opened"},
                headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "del-302-3"},
            )
            assert wh_resp.status_code == 202

            # 2. Populate ARQ queues & Redis Stream entries
            await fake_redis.rpush(QUEUE_DEFAULT, '{"id":"j1"}', '{"id":"j2"}')
            await fake_redis.rpush(QUEUE_HIGH, '{"id":"h1"}')
            await fake_redis.zadd(QUEUE_DELAYED, {"d1": 100.0})
            await fake_redis.rpush(f"{IN_FLIGHT_PREFIX}worker-1", '{"id":"if1"}')

            await fake_redis.xgroup_create(
                name=stream_key,
                groupname="outbox-consumers",
                id="0",
                mkstream=True,
            )
            entry_1 = await fake_redis.xadd(stream_key, {"event_id": "1"})
            entry_2 = await fake_redis.xadd(stream_key, {"event_id": "2"})

            mid_scrape = await client.get("/metrics")
            assert mid_scrape.status_code == 200
            mid_body = mid_scrape.text

            after_count = _find_metric_value(
                mid_body,
                "webhook_ack_latency_seconds_count",
                {"provider": "github", "status": "202"},
            )
            after_inf = _find_metric_value(
                mid_body,
                "webhook_ack_latency_seconds_bucket",
                {"provider": "github", "status": "202", "le": "+Inf"},
            )
            after_sum = _find_metric_value(
                mid_body,
                "webhook_ack_latency_seconds_sum",
                {"provider": "github", "status": "202"},
            )
            assert after_count == before_count + 1.0
            assert after_inf == after_count
            assert after_sum is not None and after_sum >= before_sum

            assert _find_metric_value(mid_body, "arq_queue_depth", {"queue": "default"}) == 2.0
            assert _find_metric_value(mid_body, "arq_queue_depth", {"queue": "high"}) == 1.0
            assert _find_metric_value(mid_body, "arq_queue_depth", {"queue": "delayed"}) == 1.0
            assert _find_metric_value(mid_body, "arq_queue_depth", {"queue": "in_flight"}) == 1.0
            assert (
                _find_metric_value(
                    mid_body,
                    "redis_stream_consumer_lag",
                    {"stream": "tenant_stream", "consumer_group": "outbox-consumers"},
                )
                == 2.0
            )

            # 3. Read without XACK (unread=1, PEL=1 -> lag remains 2.0)
            await fake_redis.xreadgroup(
                groupname="outbox-consumers",
                consumername="consumer-1",
                streams={stream_key: ">"},
                count=1,
            )
            pel_scrape = await client.get("/metrics")
            assert pel_scrape.status_code == 200
            assert (
                _find_metric_value(
                    pel_scrape.text,
                    "redis_stream_consumer_lag",
                    {"stream": "tenant_stream", "consumer_group": "outbox-consumers"},
                )
                == 2.0
            )

            # 4. Drain ARQ queues & XACK all stream entries -> gauges drop to 0.0
            await fake_redis.lrem(QUEUE_DEFAULT, 1, '{"id":"j1"}')
            await fake_redis.lrem(QUEUE_DEFAULT, 1, '{"id":"j2"}')
            await fake_redis.lrem(QUEUE_HIGH, 1, '{"id":"h1"}')
            await fake_redis.zrem(QUEUE_DELAYED, "d1")
            await fake_redis.lrem(f"{IN_FLIGHT_PREFIX}worker-1", 1, '{"id":"if1"}')

            await fake_redis.xreadgroup(
                groupname="outbox-consumers",
                consumername="consumer-1",
                streams={stream_key: ">"},
                count=1,
            )
            await fake_redis.xack(stream_key, "outbox-consumers", entry_1, entry_2)

            drained_scrape = await client.get("/metrics")
            assert drained_scrape.status_code == 200
            for q in ("default", "high", "delayed", "in_flight"):
                assert (
                    _find_metric_value(drained_scrape.text, "arq_queue_depth", {"queue": q}) == 0.0
                )
            assert (
                _find_metric_value(
                    drained_scrape.text,
                    "redis_stream_consumer_lag",
                    {"stream": "tenant_stream", "consumer_group": "outbox-consumers"},
                )
                == 0.0
            )


class TestSingleTraceIdE2EPropagation:
    """Verify single-`trace_id` continuity across HTTP, worker, outbox, consumer, and WS hops."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("use_inbound_traceparent", [False, True])
    async def test_single_trace_id_e2e_from_webhook_intake_through_worker_outbox_consumer_and_ws(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_inbound_traceparent: bool,
    ) -> None:
        shared_redis = InMemoryStreamsRedis()
        tenant_id = uuid.uuid4()
        project_id = uuid.uuid4()
        webhook_event_id = generate_uuidv7()
        recorded_outbox_rows: list[OutboxEvent] = []
        consumed_messages: list[Any] = []
        captured: dict[str, Any] = {}

        inbound_trace_id = uuid.uuid4().hex
        inbound_span_id = uuid.uuid4().hex[:16]
        inbound_traceparent = f"00-{inbound_trace_id}-{inbound_span_id}-01"

        @asynccontextmanager
        async def _fake_tenant_ctx(_tid: uuid.UUID) -> AsyncIterator[object]:
            yield object()

        async def _fake_stage(**kwargs: Any) -> WebhookStagingResult:
            return WebhookStagingResult(
                event_id=webhook_event_id,
                is_duplicate=False,
                provider=kwargs["provider"],
                delivery_id=kwargs["provider_delivery_id"],
            )

        async def _capturing_enqueue(**kwargs: Any) -> bool:
            captured["trace_id"] = get_current_trace_id_hex() or ""
            captured["envelope"] = build_webhook_envelope(
                event_id=kwargs["event_id"],
                tenant_id=kwargs["tenant_id"],
                provider=kwargs["provider"],
                event_type=kwargs["event_type"],
                payload=kwargs.get("payload") or {"event_type": kwargs["event_type"]},
            )
            return True

        monkeypatch.setattr(webhooks_router_mod, "get_tenant_db_context", _fake_tenant_ctx)
        monkeypatch.setattr(webhooks_router_mod, "stage_webhook_event_idempotent", _fake_stage)
        monkeypatch.setattr(
            webhooks_router_mod,
            "enqueue_staged_event_best_effort",
            _capturing_enqueue,
        )

        class _RelaySession:
            async def execute(self, _stmt: Any) -> _FakeDbScalarResult:
                pending = [r for r in recorded_outbox_rows if r.published_at is None]
                return _FakeDbScalarResult(rows=pending)

            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        class _OutboxStageSession:
            def add(self, obj: OutboxEvent) -> None:
                recorded_outbox_rows.append(obj)

        class _ConsumerSession:
            async def execute(self, _stmt: Any, _params: Any = None) -> _FakeDbScalarResult:
                return _FakeDbScalarResult(value=webhook_event_id)

            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        async def _worker_process_stub(_parsed_payload: dict[str, Any]) -> str:
            record_outbox_event(
                _OutboxStageSession(),  # type: ignore[arg-type]
                tenant_id=tenant_id,
                project_id=project_id,
                event_type="deployment.failed",
                aggregate_type="deployment",
                aggregate_id=uuid.uuid4(),
                payload={"status": "FAILED"},
            )
            return "processed"

        from app.ws.gateway import WebSocketGatewayManager

        with temporary_in_memory_span_exporter() as exporter:
            headers = {
                "X-GitHub-Event": "deployment_status",
                "X-GitHub-Delivery": f"del-{uuid.uuid4().hex[:8]}",
                "X-Tenant-ID": str(tenant_id),
            }
            if use_inbound_traceparent:
                headers["traceparent"] = inbound_traceparent

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/webhooks/github",
                    json={"deployment_status": {"state": "failure"}},
                    headers=headers,
                )
                assert resp.status_code == 202

            expected_trace_id = captured["trace_id"]
            assert len(expected_trace_id) == 32
            if use_inbound_traceparent:
                assert expected_trace_id == inbound_trace_id

            envelope_dict = json.loads(captured["envelope"])
            assert expected_trace_id in envelope_dict["traceparent"]
            assert get_current_trace_id_hex() is None

            # Hop 2: ARQ Worker execution
            with mock.patch(
                "app.webhooks.processor.process_webhook_job",
                side_effect=_worker_process_stub,
            ):
                worker_res = await dispatch_worker_job(envelope_dict)
                assert worker_res == "processed"

            assert len(recorded_outbox_rows) == 1
            assert expected_trace_id in recorded_outbox_rows[0].payload["traceparent"]

            # Hop 3: OutboxRelayWorker XADD to Redis stream
            relay = OutboxRelayWorker(redis_client=shared_redis)
            published = await relay.poll_tenant_batch(_RelaySession())  # type: ignore[arg-type]
            assert published == 1

            stream_key = build_stream_key(tenant_id)
            entries = await shared_redis.xrange(stream_key)
            assert len(entries) == 1
            entry_id, raw_fields = entries[0]
            assert expected_trace_id in raw_fields["traceparent"]

            # Hop 4: OutboxStreamConsumer processing
            consumer = OutboxStreamConsumer(redis_client=shared_redis)
            consume_res = await consumer.process_message(
                stream_key,
                entry_id,
                raw_fields,
                lambda _sess, msg: consumed_messages.append(msg),
                session=_ConsumerSession(),  # type: ignore[arg-type]
            )
            assert consume_res.status == "processed"
            assert len(consumed_messages) == 1

            # Hop 5: WebSocketGatewayManager live broadcast
            gateway = WebSocketGatewayManager(redis_client=shared_redis)
            live_ws = _FakeWebSocket()
            gateway._subscribers[str(tenant_id)] = {live_ws}
            gateway._cursors[str(tenant_id)] = "0-0"
            delivered = await gateway.pump_tenant_once(tenant_id, block_ms=0)
            assert delivered == 1
            assert len(live_ws.sent_frames) == 1
            assert live_ws.sent_frames[0]["trace_id"] == expected_trace_id

            # Hop 6: WebSocketGatewayManager replay delivery preserves trace_id
            replay_ws = _FakeWebSocket()
            replay_result = await gateway.replay_and_register_subscriber(
                tenant_id, replay_ws, last_seq="0-0"
            )
            assert replay_result.outcome == "replayed"
            assert replay_result.replayed_count == 1
            assert len(replay_ws.sent_frames) == 1
            assert replay_ws.sent_frames[0]["trace_id"] == expected_trace_id

            spans = exporter.get_finished_spans()
            hop_span_names = {
                "arq.worker.process",
                "outbox.relay.publish",
                "outbox.consumer.process",
                "websocket.broadcast",
            }
            observed_hop_names = {s.name for s in spans}
            assert hop_span_names.issubset(observed_hop_names), (
                f"Missing expected hop spans: {hop_span_names - observed_hop_names}"
            )
            http_spans = [s for s in spans if "/api/v1/webhooks" in s.name]
            assert http_spans, f"Missing FastAPI webhook span in {[s.name for s in spans]}"

            pipeline_spans = [
                s for s in spans if s.name in hop_span_names or "/api/v1/webhooks" in s.name
            ]
            trace_ids = {f"{s.context.trace_id:032x}" for s in pipeline_spans}
            assert trace_ids == {expected_trace_id}

    @pytest.mark.asyncio
    async def test_concurrent_traced_pipelines_do_not_bleed_trace_ids(self) -> None:
        shared_redis = InMemoryStreamsRedis()
        from app.ws.gateway import WebSocketGatewayManager

        async def _run_pipeline(trace_id_hex: str) -> tuple[str, dict[str, Any]]:
            tenant_id = uuid.uuid4()
            project_id = uuid.uuid4()
            event_id = generate_uuidv7()
            span_id_hex = uuid.uuid4().hex[:16]
            traceparent = f"00-{trace_id_hex}-{span_id_hex}-01"
            recorded_rows: list[OutboxEvent] = []

            class _StageSession:
                def add(self, obj: OutboxEvent) -> None:
                    recorded_rows.append(obj)

            class _RelaySession:
                async def execute(self, _stmt: Any) -> _FakeDbScalarResult:
                    return _FakeDbScalarResult(
                        rows=[r for r in recorded_rows if r.published_at is None]
                    )

                async def commit(self) -> None:
                    return None

                async def rollback(self) -> None:
                    return None

            envelope = json.loads(
                build_webhook_envelope(
                    event_id=event_id,
                    tenant_id=tenant_id,
                    provider="github",
                    event_type="push",
                    payload={"ref": "refs/heads/main"},
                )
            )
            envelope["traceparent"] = traceparent

            async def _worker_stub(_parsed: dict[str, Any]) -> str:
                record_outbox_event(
                    _StageSession(),  # type: ignore[arg-type]
                    tenant_id=tenant_id,
                    project_id=project_id,
                    event_type="commit.pushed",
                    aggregate_type="repository",
                    aggregate_id=uuid.uuid4(),
                    payload={"trace_marker": trace_id_hex},
                )
                return "ok"

            with mock.patch("app.webhooks.processor.process_webhook_job", side_effect=_worker_stub):
                await dispatch_worker_job(envelope)

            relay = OutboxRelayWorker(redis_client=shared_redis)
            assert await relay.poll_tenant_batch(_RelaySession()) == 1  # type: ignore[arg-type]

            gateway = WebSocketGatewayManager(redis_client=shared_redis)
            ws = _FakeWebSocket()
            gateway._subscribers[str(tenant_id)] = {ws}
            gateway._cursors[str(tenant_id)] = "0-0"
            assert await gateway.pump_tenant_once(tenant_id, block_ms=0) == 1
            return trace_id_hex, ws.sent_frames[0]

        trace_a = uuid.uuid4().hex
        trace_b = uuid.uuid4().hex

        with temporary_in_memory_span_exporter():
            (expected_a, frame_a), (expected_b, frame_b) = await asyncio.gather(
                _run_pipeline(trace_a),
                _run_pipeline(trace_b),
            )

        assert expected_a == trace_a
        assert expected_b == trace_b
        assert frame_a["trace_id"] == trace_a
        assert frame_b["trace_id"] == trace_b
        assert frame_a["trace_id"] != frame_b["trace_id"]
        assert get_current_trace_id_hex() is None
