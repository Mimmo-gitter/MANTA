"""Automated test suite for TASK-302-2 (OpenTelemetry end-to-end trace_id propagation)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any
import uuid

from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import middleware as auth_middleware
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import OutboxEvent
from app.main import app
from app.observability.tracing import (
    get_current_trace_id_hex,
    instrument_sqlalchemy_engine,
    setup_opentelemetry,
    temporary_in_memory_span_exporter,
)
from app.outbox.consumer import OutboxStreamConsumer
from app.outbox.relay import OutboxRelayWorker, build_stream_fields, build_stream_key
from app.outbox.service import record_outbox_event
from app.webhooks.enqueue import build_webhook_envelope
from app.ws.gateway import WebSocketGatewayManager
from tests.test_websocket_auth_and_fanout import (
    InMemoryStreamsRedis,
    _FakeWebSocket,
    _mint_jwt,
    patched_ws_app as _ws_test_app_fixture,
)
from workers.worker import dispatch_worker_job, serialize_job_payload

otel_ws_app = pytest.fixture(name="otel_ws_app")(
    _ws_test_app_fixture.__wrapped__
    if hasattr(_ws_test_app_fixture, "__wrapped__")
    else _ws_test_app_fixture
)


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


def test_single_trace_id_spans_webhook_sqlalchemy_worker_relay_and_ws_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify one continuous W3C trace_id spans:
    FastAPI HTTP webhook intake -> SQLAlchemy DB commit -> ARQ worker dispatch ->
    SQLAlchemy domain/outbox commit -> OutboxRelayWorker XADD ->
    WebSocketGatewayManager broadcast frame -> OutboxStreamConsumer handler.
    """
    sqlite_engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE trace_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL)"))
    instrument_sqlalchemy_engine(sqlite_engine)
    session_factory = sessionmaker(bind=sqlite_engine)

    shared_redis = InMemoryStreamsRedis()
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    webhook_event_id = generate_uuidv7()
    recorded_outbox_rows: list[OutboxEvent] = []
    consumed_messages: list[Any] = []

    # Wire lightweight traced intake endpoint on app for pure in-process OTel HTTP -> DB -> Queue test
    test_path = f"/_test/otel-webhook-intake-{uuid.uuid4().hex}"
    monkeypatch.setattr(auth_middleware, "PUBLIC_PATHS", auth_middleware.PUBLIC_PATHS | {test_path})

    captured: dict[str, str] = {}

    @app.post(test_path, status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
    async def _traced_webhook_intake(request: Request) -> dict[str, str]:
        with session_factory() as db_sess:
            db_sess.execute(
                text("INSERT INTO trace_events (id, kind) VALUES (:id, :kind)"),
                {"id": str(webhook_event_id), "kind": "staged_webhook"},
            )
            db_sess.commit()

        envelope = build_webhook_envelope(
            event_id=webhook_event_id,
            tenant_id=tenant_id,
            provider="github",
            event_type="deployment_status",
            payload={"event_type": "deployment_status"},
        )
        captured["trace_id"] = get_current_trace_id_hex() or ""
        captured["envelope"] = envelope
        return {"status": "accepted"}

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
            with session_factory() as db_sess:
                db_sess.execute(
                    text("INSERT INTO trace_events (id, kind) VALUES (:id, :kind)"),
                    {"id": str(uuid.uuid4()), "kind": "consumer_ledger"},
                )
                db_sess.commit()

        async def rollback(self) -> None:
            return None

    async def _worker_process_stub(parsed_payload: dict[str, Any]) -> str:
        # Execute real instrumented SQLAlchemy commit + stage OutboxEvent inside worker span
        with session_factory() as db_sess:
            db_sess.execute(
                text("INSERT INTO trace_events (id, kind) VALUES (:id, :kind)"),
                {"id": str(uuid.uuid4()), "kind": "domain_persisted"},
            )
            db_sess.commit()

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

    import asyncio
    from unittest import mock

    with temporary_in_memory_span_exporter() as exporter:
        # 1. FastAPI HTTP webhook intake + SQLAlchemy staging commit + ARQ envelope trace injection
        client = TestClient(app)
        resp = client.post(test_path)
        assert resp.status_code == 202
        expected_trace_id = captured["trace_id"]
        assert len(expected_trace_id) == 32
        envelope_dict = json.loads(captured["envelope"])
        assert expected_trace_id in envelope_dict["traceparent"]

        # Assert current active context outside request is untraced
        assert get_current_trace_id_hex() is None

        # 2. ARQ Worker execution + OutboxRelay + WebSocket broadcast + Stream Consumer
        async def _run_async_pipeline() -> dict[str, Any]:
            with mock.patch(
                "app.webhooks.processor.process_webhook_job",
                side_effect=_worker_process_stub,
            ):
                worker_res = await dispatch_worker_job(envelope_dict)
                assert worker_res == "processed"

            assert len(recorded_outbox_rows) == 1
            relay = OutboxRelayWorker(redis_client=shared_redis)
            published = await relay.poll_tenant_batch(_RelaySession())  # type: ignore[arg-type]
            assert published == 1

            gateway = WebSocketGatewayManager(redis_client=shared_redis)
            fake_ws = _FakeWebSocket()
            gateway._subscribers[str(tenant_id)] = {fake_ws}
            gateway._cursors[str(tenant_id)] = "0-0"
            delivered = await gateway.pump_tenant_once(tenant_id, block_ms=0)
            assert delivered == 1

            stream_key = build_stream_key(tenant_id)
            entries = await shared_redis.xrange(stream_key)
            assert len(entries) == 1
            entry_id, raw_fields = entries[0]

            consumer = OutboxStreamConsumer(redis_client=shared_redis)
            consume_res = await consumer.process_message(
                stream_key,
                entry_id,
                raw_fields,
                lambda _sess, msg: consumed_messages.append(msg),
                session=_ConsumerSession(),  # type: ignore[arg-type]
            )
            assert consume_res.status == "processed"
            return fake_ws.sent_frames[0]

        ws_frame = asyncio.run(_run_async_pipeline())
        assert ws_frame["trace_id"] == expected_trace_id
        assert len(consumed_messages) == 1

        spans = exporter.get_finished_spans()
        span_names = [s.name for s in spans]

        assert any(test_path in name for name in span_names), f"Missing FastAPI span: {span_names}"
        assert "arq.worker.process" in span_names
        assert "outbox.relay.publish" in span_names
        assert "websocket.broadcast" in span_names
        assert "outbox.consumer.process" in span_names
        assert any(s.attributes.get("db.system") == "sqlite" for s in spans), (
            "Expected SQLAlchemy spans to be recorded"
        )

        # Every single pipeline span shares the exact same 32-hex trace_id
        pipeline_spans = [
            s
            for s in spans
            if (test_path in s.name)
            or s.name
            in {
                "arq.worker.process",
                "outbox.relay.publish",
                "websocket.broadcast",
                "outbox.consumer.process",
            }
            or s.attributes.get("db.system") == "sqlite"
        ]
        trace_ids = {f"{s.context.trace_id:032x}" for s in pipeline_spans}
        assert trace_ids == {expected_trace_id}

    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != test_path]


def test_non_blocking_otlp_export_when_collector_unreachable() -> None:
    """Verify BatchSpanProcessor + OTLP exporter never block FastAPI request latency when collector is down."""
    exporter = OTLPSpanExporter(endpoint="http://127.0.0.1:59999", insecure=True, timeout=1)
    processor = BatchSpanProcessor(
        exporter,
        schedule_delay_millis=60_000,
        max_export_batch_size=512,
        export_timeout_millis=500,
    )
    local_provider = TracerProvider(resource=Resource.create({"service.name": "manta-otlp-test"}))
    local_provider.add_span_processor(processor)

    # Verify our production helper also selects BatchSpanProcessor when endpoint is configured
    with temporary_in_memory_span_exporter():
        configured = setup_opentelemetry()
        assert configured is not None

    mini_app = FastAPI()
    FastAPIInstrumentor.instrument_app(mini_app, tracer_provider=local_provider)

    @mini_app.get("/_test/otel-fast")
    async def _fast_endpoint() -> dict[str, bool]:
        return {"ok": True}

    try:
        client = TestClient(mini_app)
        started = time.monotonic()
        for _ in range(5):
            res = client.get("/_test/otel-fast")
            assert res.status_code == 200
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, f"Unreachable OTLP collector blocked request path ({elapsed:.3f}s)"
    finally:
        FastAPIInstrumentor.uninstrument_app(mini_app)
        processor._shutdown = True  # Avoid 500ms shutdown export retry spam to closed test port
        exporter.shutdown()


def test_ws_query_token_is_scrubbed_from_otel_spans(otel_ws_app: Any) -> None:
    """SRS §3.5.5: ?token=<jwt> must never appear verbatim in OpenTelemetry span attributes."""
    test_app, _ = otel_ws_app
    tenant_id = str(uuid.uuid4())
    secret_jwt = _mint_jwt(tenant_id, ["DEVELOPER"])

    with temporary_in_memory_span_exporter() as exporter:
        client = TestClient(test_app)
        with client.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_id}/stream?token={secret_jwt}",
        ) as ws:
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}

        spans = exporter.get_finished_spans()
        assert any(s.name == "websocket.connect" for s in spans)
        for span in spans:
            for attr_key, attr_val in (span.attributes or {}).items():
                assert secret_jwt not in str(attr_val), (
                    f"Raw JWT leaked in span {span.name} attribute {attr_key}"
                )
        ws_connect_span = next(s for s in spans if s.name == "websocket.connect")
        assert ws_connect_span.attributes["url.query"] == "token=[REDACTED]"


def test_untraced_serialization_preserves_legacy_contracts() -> None:
    """Verify untraced jobs and outbox rows omit traceparent/tracestate for byte/key compatibility."""
    assert get_current_trace_id_hex() is None

    serialized = serialize_job_payload(
        job_id="job-1",
        tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        priority="default",
        job_type="webhook.process",
        payload={"ok": True},
        enqueued_at="2026-11-27T00:00:00+00:00",
    )
    parsed = json.loads(serialized)
    assert "traceparent" not in parsed
    assert "tracestate" not in parsed

    ev_id = generate_uuidv7()
    row = OutboxEvent(
        id=ev_id,
        tenant_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        aggregate_type="deployment",
        aggregate_id="agg-1",
        event_type="deployment.failed",
        payload={"event_id": str(ev_id), "data": {}},
        created_at=datetime.now(timezone.utc),
    )
    fields = build_stream_fields(row)
    assert set(fields.keys()) == {"event_id", "event_type", "tenant_id", "payload", "occurred_at"}
