"""
TASK-303-3 (SCRUM-530 / US-34): 2-Pod WebSocket <2s Delivery, Ordering & Cross-Tenant 403 Rejection.

Integration test suite verifying:
1. Two simulated backend pods (`pod_1`, `pod_2`) deliver ordered Tenant A alerts
   within the `<= 2.0s` SLO.
2. Strict monotonic ordering (`seq` and UUIDv7 `event_id`) across both pods.
3. Tenant B subscribers on both pods receive zero frames when Tenant A events fire,
   and interleaved Tenant A + Tenant B events stay strictly isolated.
4. Real 2-pod FastAPI ASGI apps running `/api/v1/ws/tenants/{tenant_id}/stream`
   deliver live events within `<= 2.0s` and reject cross-tenant subscriptions
   pre-accept (`WS 1008` / HTTP 403) across all token transports.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
import httpx
import pytest

from app.auth.jwks import JWKSClient
from app.auth.middleware import JWTAuthMiddleware
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import OutboxEvent
from app.outbox.relay import OutboxRelayWorker
from app.outbox.service import record_outbox_event
from app.ws.gateway import WebSocketGatewayManager
from app.ws.router import router as ws_router
from tests.test_websocket_auth_and_fanout import (
    InMemoryStreamsRedis,
    _FakeWebSocket,
    _RecordingRelaySession,
    _jwks_handler,
    _mint_jwt,
)


def _build_pod_ws_app(shared_redis: InMemoryStreamsRedis) -> FastAPI:
    """Construct an isolated FastAPI pod app with its own WebSocketGatewayManager."""

    @asynccontextmanager
    async def _lifespan(pod_app: FastAPI) -> AsyncIterator[None]:
        gateway = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)
        pod_app.state.ws_gateway = gateway
        try:
            yield
        finally:
            await gateway.shutdown()

    pod_app = FastAPI(lifespan=_lifespan)
    pod_app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_jwks_handler),
    )
    pod_app.add_middleware(JWTAuthMiddleware)
    pod_app.include_router(ws_router)
    return pod_app


def _patch_ws_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch DB role lookup and OIDC issuer/audience for deterministic RS256 WS tests."""

    @asynccontextmanager
    async def _no_db_ctx(_tenant_uuid: Any) -> AsyncIterator[Any]:
        raise OSError("DB unavailable in unit mock; fallback to token claims")
        yield

    monkeypatch.setattr("app.auth.permissions.get_tenant_db_context", _no_db_ctx)


def _stage_and_build_events(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    count: int,
    *,
    event_types: tuple[str, ...] = ("deployment.failed", "incident.spike", "health.degraded"),
) -> list[OutboxEvent]:
    """Stage `count` monotonic UUIDv7 OutboxEvent rows for a tenant."""
    staged: list[OutboxEvent] = []

    class _StageSession:
        def add(self, obj: OutboxEvent) -> None:
            staged.append(obj)

    db = _StageSession()
    for idx in range(count):
        record_outbox_event(
            db,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            project_id=project_id,
            event_id=generate_uuidv7(),
            event_type=event_types[idx % len(event_types)],
            aggregate_type="deployment",
            aggregate_id=uuid.uuid4(),
            payload={"ordinal": idx, "tenant_marker": str(tenant_id)},
        )
    return staged


@pytest.mark.asyncio
async def test_two_pods_deliver_ordered_tenant_a_alerts_within_2s_and_isolate_tenant_b() -> None:
    shared_redis = InMemoryStreamsRedis()
    pod_1 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)
    pod_2 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    project_a = uuid.uuid4()

    ws_a_pod1 = _FakeWebSocket()
    ws_a_pod2 = _FakeWebSocket()
    ws_b_pod1 = _FakeWebSocket()
    ws_b_pod2 = _FakeWebSocket()

    await pod_1.register_subscriber(tenant_a, ws_a_pod1)
    await pod_2.register_subscriber(tenant_a, ws_a_pod2)
    await pod_1.register_subscriber(tenant_b, ws_b_pod1)
    await pod_2.register_subscriber(tenant_b, ws_b_pod2)

    try:
        rows = _stage_and_build_events(tenant_a, project_a, count=10)
        relay = OutboxRelayWorker(redis_client=shared_redis)

        started = time.monotonic()
        published = await relay.poll_tenant_batch(_RecordingRelaySession(rows))  # type: ignore[arg-type]
        assert published == 10

        deadline = started + 2.0
        while time.monotonic() < deadline:
            if len(ws_a_pod1.sent_frames) == 10 and len(ws_a_pod2.sent_frames) == 10:
                break
            await asyncio.sleep(0.01)

        elapsed = time.monotonic() - started
        assert elapsed <= 2.0, f"2-pod delivery exceeded 2.0s SLO ({elapsed:.3f}s)"

        for frames in (ws_a_pod1.sent_frames, ws_a_pod2.sent_frames):
            assert len(frames) == 10
            assert [f["data"]["ordinal"] for f in frames] == list(range(10))
            assert [f["event_id"] for f in frames] == [str(r.id) for r in rows]
            seqs = [tuple(int(p) for p in f["seq"].split("-")) for f in frames]
            assert seqs == sorted(seqs)
            assert all(f["tenant_id"] == str(tenant_a) for f in frames)

        # Tenant B must receive zero frames on both pods
        assert ws_b_pod1.sent_frames == []
        assert ws_b_pod2.sent_frames == []
    finally:
        await pod_1.shutdown()
        await pod_2.shutdown()


@pytest.mark.asyncio
async def test_two_pods_interleaved_bidirectional_tenant_isolation() -> None:
    shared_redis = InMemoryStreamsRedis()
    pod_1 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)
    pod_2 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    ws_a_pod1, ws_a_pod2 = _FakeWebSocket(), _FakeWebSocket()
    ws_b_pod1, ws_b_pod2 = _FakeWebSocket(), _FakeWebSocket()

    await pod_1.register_subscriber(tenant_a, ws_a_pod1)
    await pod_2.register_subscriber(tenant_a, ws_a_pod2)
    await pod_1.register_subscriber(tenant_b, ws_b_pod1)
    await pod_2.register_subscriber(tenant_b, ws_b_pod2)

    try:
        rows_a = _stage_and_build_events(tenant_a, uuid.uuid4(), count=4)
        rows_b = _stage_and_build_events(tenant_b, uuid.uuid4(), count=4)

        relay = OutboxRelayWorker(redis_client=shared_redis)
        started = time.monotonic()
        assert await relay.poll_tenant_batch(_RecordingRelaySession(rows_a)) == 4  # type: ignore[arg-type]
        assert await relay.poll_tenant_batch(_RecordingRelaySession(rows_b)) == 4  # type: ignore[arg-type]

        deadline = started + 2.0
        while time.monotonic() < deadline:
            if (
                len(ws_a_pod1.sent_frames) == 4
                and len(ws_a_pod2.sent_frames) == 4
                and len(ws_b_pod1.sent_frames) == 4
                and len(ws_b_pod2.sent_frames) == 4
            ):
                break
            await asyncio.sleep(0.01)

        assert (time.monotonic() - started) <= 2.0

        for frames in (ws_a_pod1.sent_frames, ws_a_pod2.sent_frames):
            assert [f["data"]["ordinal"] for f in frames] == [0, 1, 2, 3]
            assert {f["tenant_id"] for f in frames} == {str(tenant_a)}

        for frames in (ws_b_pod1.sent_frames, ws_b_pod2.sent_frames):
            assert [f["data"]["ordinal"] for f in frames] == [0, 1, 2, 3]
            assert {f["tenant_id"] for f in frames} == {str(tenant_b)}
    finally:
        await pod_1.shutdown()
        await pod_2.shutdown()


def test_two_pod_asgi_apps_deliver_live_alerts_within_2s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ws_auth(monkeypatch)
    shared_redis = InMemoryStreamsRedis()
    pod_1_app = _build_pod_ws_app(shared_redis)
    pod_2_app = _build_pod_ws_app(shared_redis)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    token_a = _mint_jwt(tenant_a, ["DEVELOPER"])
    token_b = _mint_jwt(tenant_b, ["DEVELOPER"])

    with (
        TestClient(pod_1_app) as client_pod1,
        TestClient(pod_2_app) as client_pod2,
        client_pod1.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream",
            headers={"Authorization": f"Bearer {token_a}"},
        ) as ws_a1,
        client_pod2.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream",
            headers={"Authorization": f"Bearer {token_a}"},
        ) as ws_a2,
        client_pod2.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_b}/stream",
            headers={"Authorization": f"Bearer {token_b}"},
        ) as ws_b2,
    ):
        rows = _stage_and_build_events(tenant_a, uuid.uuid4(), count=3)
        relay = OutboxRelayWorker(redis_client=shared_redis)

        async def _relay_and_wait() -> float:
            started = time.monotonic()
            published = await relay.poll_tenant_batch(_RecordingRelaySession(rows))  # type: ignore[arg-type]
            assert published == 3
            return started

        # Run relay from a background thread or portal on pod_1_app
        started = client_pod1.portal.call(_relay_and_wait)  # type: ignore[union-attr]

        frames_pod1 = [ws_a1.receive_json() for _ in range(3)]
        frames_pod2 = [ws_a2.receive_json() for _ in range(3)]
        elapsed = time.monotonic() - started
        assert elapsed <= 2.0, f"2-pod ASGI delivery exceeded 2.0s SLO ({elapsed:.3f}s)"

        for frames in (frames_pod1, frames_pod2):
            assert [f["data"]["ordinal"] for f in frames] == [0, 1, 2]
            assert [f["event_id"] for f in frames] == [str(r.id) for r in rows]
            assert all(f["tenant_id"] == str(tenant_a) for f in frames)

        # Verify Tenant B socket is alive (responds to ping) and received zero Tenant A events
        ws_b2.send_text("ping")
        assert ws_b2.receive_json() == {"type": "pong"}


def test_cross_tenant_ws_subscription_rejected_pre_accept_across_pods_and_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ws_auth(monkeypatch)
    shared_redis = InMemoryStreamsRedis()
    pod_1_app = _build_pod_ws_app(shared_redis)
    pod_2_app = _build_pod_ws_app(shared_redis)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    tenant_b_token = _mint_jwt(tenant_b, ["DEVELOPER"])

    for pod_app in (pod_1_app, pod_2_app):
        with TestClient(pod_app) as client:
            # 1. Authorization: Bearer <jwt>
            with pytest.raises(WebSocketDisconnect) as exc_hdr:
                with client.websocket_connect(
                    f"/api/v1/ws/tenants/{tenant_a}/stream",
                    headers={"Authorization": f"Bearer {tenant_b_token}"},
                ):
                    pass
            assert exc_hdr.value.code == 1008

            # 2. ?token=<jwt>
            with pytest.raises(WebSocketDisconnect) as exc_qry:
                with client.websocket_connect(
                    f"/api/v1/ws/tenants/{tenant_a}/stream?token={tenant_b_token}",
                ):
                    pass
            assert exc_qry.value.code == 1008

            # 3. Sec-WebSocket-Protocol: bearer, <jwt>
            with pytest.raises(WebSocketDisconnect) as exc_sub:
                with client.websocket_connect(
                    f"/api/v1/ws/tenants/{tenant_a}/stream",
                    subprotocols=["bearer", tenant_b_token],
                ):
                    pass
            assert exc_sub.value.code == 1008

            assert pod_app.state.ws_gateway._subscribers.get(str(tenant_a)) in (None, set())
