"""Automated test suite for TASK-303-1 (WS Auth/RBAC/Cross-Tenant) and TASK-303-2 (Multi-Pod Fanout)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any
from unittest import mock
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from redis.exceptions import ResponseError
from starlette.routing import WebSocketRoute

from app.auth.jwks import JWKSClient
from app.auth.permissions import (
    Role,
    extract_all_app_routes,
    validate_registered_routes_completeness,
)
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import (
    Deployment,
    DeploymentIncident,
    Issue,
    OutboxEvent,
    ProductionFailure,
    Project,
)
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.deployments.service import persist_deployment_idempotent
from app.jira.incident_correlation import correlate_incident
from app.main import app
from app.outbox.relay import OutboxRelayWorker, build_stream_key
from app.outbox.service import record_outbox_event
from app.projects.health_service import process_health_signal
from app.projects.schemas import HealthSignalStatus
from app.ws.gateway import WebSocketGatewayManager

_TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_PUBLIC_JWK = json.loads(RSAAlgorithm.to_jwk(_TEST_PRIVATE_KEY.public_key()))
_TEST_PUBLIC_JWK.update({"kid": "ws-test-kid", "kty": "RSA", "alg": "RS256", "use": "sig"})


def _jwks_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"keys": [_TEST_PUBLIC_JWK]}, request=request)


def _mint_jwt(
    tenant_id: uuid.UUID | str,
    roles: list[str],
    *,
    sub: str | None = None,
    iss: str = "http://localhost:8080/realms/manta",
    aud: str = "manta-backend",
    exp_delta_seconds: int = 300,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": sub if sub is not None else str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "roles": roles,
        "iss": iss,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta_seconds)).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(
        payload,
        _TEST_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": "ws-test-kid"},
    )


class InMemoryStreamsRedis:
    """Thread-safe async in-memory Redis Streams stub shared across simulated pods."""

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._entries_added: dict[str, int] = {}
        self._max_deleted_id: dict[str, str] = {}
        self._kv: dict[str, str] = {}
        self._seq = 0

    @staticmethod
    def _parse_id(eid: str) -> tuple[int, int]:
        parts = eid.split("-", 1)
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._seq += 1
        entry_id = f"{1_795_768_000_000 + self._seq}-0"
        stream = self._streams.setdefault(name, [])
        stream.append((entry_id, {str(k): str(v) for k, v in fields.items()}))
        self._entries_added[name] = self._entries_added.get(name, 0) + 1
        if maxlen is not None and maxlen > 0 and len(stream) > maxlen:
            trim_count = len(stream) - maxlen
            self._max_deleted_id[name] = stream[trim_count - 1][0]
            del stream[:trim_count]
        return entry_id

    async def xtrim(
        self,
        name: str,
        *,
        maxlen: int,
        approximate: bool = False,
    ) -> int:
        stream = self._streams.get(name, [])
        if maxlen < 0 or len(stream) <= maxlen:
            return 0
        trim_count = len(stream) - maxlen
        if trim_count > 0:
            self._max_deleted_id[name] = stream[trim_count - 1][0]
            del stream[:trim_count]
        return trim_count

    async def xinfo_stream(self, name: str) -> dict[str, Any]:
        if name not in self._streams:
            raise ResponseError("ERR no such key")
        entries = self._streams[name]
        return {
            "length": len(entries),
            "entries-added": self._entries_added.get(name, len(entries)),
            "max-deleted-entry-id": self._max_deleted_id.get(name, "0-0"),
            "first-entry": entries[0] if entries else None,
            "last-entry": entries[-1] if entries else None,
        }

    def _filter_range(
        self,
        entries: list[tuple[str, dict[str, str]]],
        *,
        min_id: str,
        max_id: str,
    ) -> list[tuple[str, dict[str, str]]]:
        min_exclusive = min_id.startswith("(")
        max_exclusive = max_id.startswith("(")
        min_clean = min_id[1:] if min_exclusive else min_id
        max_clean = max_id[1:] if max_exclusive else max_id
        min_tuple = None if min_clean == "-" else self._parse_id(min_clean)
        max_tuple = None if max_clean == "+" else self._parse_id(max_clean)

        matched: list[tuple[str, dict[str, str]]] = []
        for eid, flds in entries:
            eid_tuple = self._parse_id(eid)
            if min_tuple is not None:
                if min_exclusive and eid_tuple <= min_tuple:
                    continue
                if not min_exclusive and eid_tuple < min_tuple:
                    continue
            if max_tuple is not None:
                if max_exclusive and eid_tuple >= max_tuple:
                    continue
                if not max_exclusive and eid_tuple > max_tuple:
                    continue
            matched.append((eid, flds))
        return matched

    async def xrevrange(
        self,
        name: str,
        max: str = "+",
        min: str = "-",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        filtered = self._filter_range(self._streams.get(name, []), min_id=min, max_id=max)
        entries = list(reversed(filtered))
        if count is not None:
            entries = entries[:count]
        return entries

    async def xrange(
        self,
        name: str,
        min: str = "-",
        max: str = "+",
        count: int | None = None,
    ) -> list[tuple[str, dict[str, str]]]:
        entries = self._filter_range(self._streams.get(name, []), min_id=min, max_id=max)
        if count is not None:
            entries = entries[:count]
        return entries

    async def xread(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        results: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for skey, cursor in streams.items():
            entries = self._streams.get(skey, [])
            if cursor == "$":
                matched: list[tuple[str, dict[str, str]]] = []
            elif cursor in ("0", "0-0"):
                matched = list(entries)
            else:
                cursor_tuple = tuple(int(p) for p in cursor.split("-", 1))
                matched = [
                    (eid, flds)
                    for eid, flds in entries
                    if tuple(int(p) for p in eid.split("-", 1)) > cursor_tuple
                ]
            if count is not None and count > 0:
                matched = matched[:count]
            if matched:
                results.append((skey, matched))
        if not results and block and block > 0:
            await asyncio.sleep(min(block / 1000.0, 0.02))
        return results

    async def xgroup_create(
        self,
        *,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> None:
        if mkstream:
            self._streams.setdefault(name, [])

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        return len(ids)

    async def exists(self, key: str) -> int:
        return 1 if key in self._kv else 0

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        if nx and key in self._kv:
            return False
        self._kv[key] = value
        return True

    async def aclose(self) -> None:
        return None


class _FakeWebSocket:
    """In-memory async WebSocket recorder simulating connected clients across pods."""

    def __init__(self, *, fail_on_send: bool = False, hang_seconds: float = 0.0) -> None:
        self.sent_frames: list[dict[str, Any]] = []
        self.fail_on_send = fail_on_send
        self.hang_seconds = hang_seconds
        self.closed = False

    async def send_text(self, data: str) -> None:
        if self.hang_seconds > 0:
            await asyncio.sleep(self.hang_seconds)
        if self.fail_on_send:
            raise RuntimeError("Simulated broken WebSocket pipe")
        self.sent_frames.append(json.loads(data))


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> "_ScalarResult":
        return self

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def all(self) -> list[Any]:
        return list(self._items)


class _RecordingRelaySession:
    def __init__(self, rows: list[OutboxEvent]) -> None:
        self.rows = rows
        self.committed = False

    async def execute(self, _stmt: Any) -> _ScalarResult:
        pending = sorted(
            (r for r in self.rows if r.published_at is None),
            key=lambda r: r.id,
        )
        return _ScalarResult(pending[:100])

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        for row in self.rows:
            row.published_at = None


@pytest.fixture
def patched_ws_app():
    orig_jwks = getattr(app.state, "jwks_client", None)
    orig_gateway = getattr(app.state, "ws_gateway", None)
    shared_redis = InMemoryStreamsRedis()

    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_jwks_handler),
    )
    app.state.ws_gateway = WebSocketGatewayManager(redis_client=shared_redis)

    @asynccontextmanager
    async def _no_db_ctx(_tenant_uuid: Any):
        raise OSError("DB unavailable in unit mock; fallback to token claims")
        yield

    with mock.patch("app.auth.permissions.get_tenant_db_context", _no_db_ctx):
        yield app, shared_redis

    app.state.jwks_client = orig_jwks
    app.state.ws_gateway = orig_gateway


async def _raw_asgi_ws_handshake(
    path: str,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> list[dict[str, Any]]:
    """Drive Starlette/FastAPI ASGI WebSocket lifespan directly to prove pre-accept rejection."""
    sent_messages: list[dict[str, Any]] = []
    incoming = asyncio.Queue[dict[str, Any]]()
    await incoming.put({"type": "websocket.connect"})
    await incoming.put({"type": "websocket.disconnect", "code": 1000})

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_string,
        "headers": headers or [],
        "client": ("127.0.0.1", 55000),
        "server": ("testserver", 80),
        "subprotocols": [],
        "state": {},
    }

    async def receive() -> dict[str, Any]:
        return await incoming.get()

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    await app(scope, receive, send)
    return sent_messages


# ==============================================================================
# TASK-303-1: WebSocket RS256 Handshake Auth, RBAC & Cross-Tenant Isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_ws_rejects_unauthenticated_before_accept(patched_ws_app: Any) -> None:
    tenant_a = str(uuid.uuid4())
    messages = await _raw_asgi_ws_handshake(f"/api/v1/ws/tenants/{tenant_a}/stream")

    assert all(msg["type"] != "websocket.accept" for msg in messages)
    assert any(msg["type"] == "websocket.close" and msg.get("code") == 1008 for msg in messages)


@pytest.mark.asyncio
async def test_ws_rejects_expired_tampered_wrong_aud_iss_and_bad_claims(
    patched_ws_app: Any,
) -> None:
    tenant_a = str(uuid.uuid4())
    expired_tok = _mint_jwt(tenant_a, ["DEVELOPER"], exp_delta_seconds=-60)
    wrong_aud = _mint_jwt(tenant_a, ["DEVELOPER"], aud="wrong-aud")
    wrong_iss = _mint_jwt(tenant_a, ["DEVELOPER"], iss="https://evil.idp/realms/fake")
    bad_tenant_claim = _mint_jwt("not-a-uuid", ["DEVELOPER"])
    empty_sub_claim = _mint_jwt(tenant_a, ["DEVELOPER"], sub="   ")

    for bad_token in (
        "not.a.jwt",
        expired_tok,
        wrong_aud,
        wrong_iss,
        bad_tenant_claim,
        empty_sub_claim,
    ):
        messages = await _raw_asgi_ws_handshake(
            f"/api/v1/ws/tenants/{tenant_a}/stream",
            headers=[(b"authorization", f"Bearer {bad_token}".encode("utf-8"))],
        )
        assert all(msg["type"] != "websocket.accept" for msg in messages)
        assert any(msg["type"] == "websocket.close" and msg.get("code") == 1008 for msg in messages)


def test_ws_rejects_cross_tenant_subscription_consistently(patched_ws_app: Any) -> None:
    test_app, _ = patched_ws_app
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    # Even TENANT_ADMIN of Tenant B must be rejected from Tenant A's stream
    tenant_b_token = _mint_jwt(tenant_b, ["TENANT_ADMIN"])

    client = TestClient(test_app)

    # 1. Authorization header
    with pytest.raises(WebSocketDisconnect) as exc1:
        with client.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream",
            headers={"Authorization": f"Bearer {tenant_b_token}"},
        ):
            pass
    assert exc1.value.code == 1008

    # 2. ?token= query parameter
    with pytest.raises(WebSocketDisconnect) as exc2:
        with client.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream?token={tenant_b_token}",
        ):
            pass
    assert exc2.value.code == 1008

    # 3. Sec-WebSocket-Protocol header
    with pytest.raises(WebSocketDisconnect) as exc3:
        with client.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream",
            subprotocols=["bearer", tenant_b_token],
        ):
            pass
    assert exc3.value.code == 1008


def test_ws_rejects_empty_unknown_and_deactivated_roles(patched_ws_app: Any) -> None:
    test_app, _ = patched_ws_app
    tenant_a = str(uuid.uuid4())
    client = TestClient(test_app)

    for roles in ([], ["GUEST"]):
        token = _mint_jwt(tenant_a, roles)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/ws/tenants/{tenant_a}/stream",
                headers={"Authorization": f"Bearer {token}"},
            ):
                pass
        assert exc.value.code == 1008

    # Active role in JWT, but DB says user is deactivated (`is_active=False` -> empty roles)
    dev_token = _mint_jwt(tenant_a, ["DEVELOPER"])

    class _FakeDbResult:
        def all(self) -> list[tuple[str, bool, str | None]]:
            return [("DEVELOPER", False, None)]

    class _DeactivatedSession:
        async def execute(self, _stmt: Any) -> _FakeDbResult:
            return _FakeDbResult()

    @asynccontextmanager
    async def _deactivated_db_ctx(_tenant_uuid: Any):
        yield _DeactivatedSession()

    with mock.patch("app.auth.permissions.get_tenant_db_context", _deactivated_db_ctx):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/ws/tenants/{tenant_a}/stream",
                headers={"Authorization": f"Bearer {dev_token}"},
            ):
                pass
        assert exc.value.code == 1008


@pytest.mark.parametrize("role", [r.value for r in Role])
def test_ws_accepts_all_seven_roles_and_supported_token_transports(
    patched_ws_app: Any,
    role: str,
) -> None:
    test_app, _ = patched_ws_app
    tenant_a = str(uuid.uuid4())
    token = _mint_jwt(tenant_a, [role])
    client = TestClient(test_app)

    # Authorization header
    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}

    # ?token= query parameter + JSON ping
    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream?token={token}",
    ) as ws:
        ws.send_text('{"type":"ping"}')
        assert ws.receive_json() == {"type": "pong"}

    # Sec-WebSocket-Protocol: bearer, <jwt>
    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream",
        subprotocols=["bearer", token],
    ) as ws:
        assert ws.accepted_subprotocol == "bearer"
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


def test_validate_registered_routes_completeness_includes_ws_stream() -> None:
    ws_routes = [
        (path, route)
        for path, route in extract_all_app_routes(app)
        if isinstance(route, WebSocketRoute) and path == "/api/v1/ws/tenants/{tenant_id}/stream"
    ]
    assert len(ws_routes) == 1
    assert validate_registered_routes_completeness(app) == []


# ==============================================================================
# TASK-303-2: Multi-Pod Redis Streams Fanout & Domain Outbox Producers
# ==============================================================================


@pytest.mark.asyncio
async def test_multipod_fanout_delivers_ordered_events_within_2s_and_isolates_tenants() -> None:
    shared_redis = InMemoryStreamsRedis()
    pod_1 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)
    pod_2 = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    project_id = uuid.uuid4()

    ws_a_pod1 = _FakeWebSocket()
    ws_a_pod2 = _FakeWebSocket()
    ws_b_pod1 = _FakeWebSocket()
    ws_b_pod2 = _FakeWebSocket()

    await pod_1.register_subscriber(tenant_a, ws_a_pod1)
    await pod_2.register_subscriber(tenant_a, ws_a_pod2)
    await pod_1.register_subscriber(tenant_b, ws_b_pod1)
    await pod_2.register_subscriber(tenant_b, ws_b_pod2)

    try:
        # Stage 5 ordered Tenant A outbox events and relay them to Redis Stream
        rows: list[OutboxEvent] = []
        session_recorder: list[OutboxEvent] = []

        class _StageSession:
            def add(self, obj: OutboxEvent) -> None:
                session_recorder.append(obj)

        stage_db = _StageSession()
        for seq_idx in range(5):
            ev_id = generate_uuidv7()
            row = record_outbox_event(
                stage_db,  # type: ignore[arg-type]
                tenant_id=tenant_a,
                project_id=project_id,
                event_id=ev_id,
                event_type="deployment.failed" if seq_idx % 2 == 0 else "deployment.succeeded",
                aggregate_type="deployment",
                aggregate_id=uuid.uuid4(),
                payload={"ordinal": seq_idx},
            )
            rows.append(row)

        relay = OutboxRelayWorker(redis_client=shared_redis)
        published_count = await relay.poll_tenant_batch(_RecordingRelaySession(rows))  # type: ignore[arg-type]
        assert published_count == 5

        started = time.monotonic()
        deadline = started + 2.0
        while time.monotonic() < deadline:
            if len(ws_a_pod1.sent_frames) == 5 and len(ws_a_pod2.sent_frames) == 5:
                break
            await asyncio.sleep(0.01)

        elapsed = time.monotonic() - started
        assert elapsed <= 2.0, f"Multi-pod fanout exceeded 2s SLO ({elapsed:.3f}s)"

        for subscriber_frames in (ws_a_pod1.sent_frames, ws_a_pod2.sent_frames):
            assert len(subscriber_frames) == 5
            assert [f["data"]["ordinal"] for f in subscriber_frames] == [0, 1, 2, 3, 4]
            assert [f["event_id"] for f in subscriber_frames] == [str(r.id) for r in rows]
            seqs = [tuple(int(p) for p in f["seq"].split("-")) for f in subscriber_frames]
            assert seqs == sorted(seqs)
            for frame in subscriber_frames:
                assert frame["type"] == "event"
                assert frame["schema_version"] == "1.0"
                assert frame["tenant_id"] == str(tenant_a)
                assert frame["project_id"] == str(project_id)
                assert frame["occurred_at"]

        # Strict tenant boundary isolation across all pods
        assert ws_b_pod1.sent_frames == []
        assert ws_b_pod2.sent_frames == []
    finally:
        await pod_1.shutdown()
        await pod_2.shutdown()


@pytest.mark.asyncio
async def test_gateway_suppresses_duplicate_event_ids_and_evicts_stalled_sockets() -> None:
    shared_redis = InMemoryStreamsRedis()
    pod = WebSocketGatewayManager(
        redis_client=shared_redis,
        xread_block_ms=10,
        send_timeout_seconds=0.05,
    )
    tenant_a = uuid.uuid4()
    ws_healthy = _FakeWebSocket()
    ws_broken = _FakeWebSocket(fail_on_send=True)
    ws_stalled = _FakeWebSocket(hang_seconds=0.3)

    # Register subscribers with manual pumping so background loop doesn't race assertions
    pod._subscribers[str(tenant_a)] = {ws_healthy, ws_broken, ws_stalled}
    pod._cursors[str(tenant_a)] = "0-0"

    dup_event_id = str(generate_uuidv7())
    stream_key = build_stream_key(tenant_a)
    for attempt in range(2):
        await shared_redis.xadd(
            stream_key,
            {
                "event_id": dup_event_id,
                "event_type": "incident.opened",
                "tenant_id": str(tenant_a),
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "payload": json.dumps(
                    {
                        "event_id": dup_event_id,
                        "event_type": "incident.opened",
                        "schema_version": "1.0",
                        "tenant_id": str(tenant_a),
                        "project_id": None,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "data": {"attempt": attempt},
                    }
                ),
            },
        )

    delivered = await pod.pump_tenant_once(tenant_a, block_ms=0)
    assert delivered == 1
    assert len(ws_healthy.sent_frames) == 1
    assert ws_healthy.sent_frames[0]["event_id"] == dup_event_id
    # Broken and stalled sockets evicted cleanly without raising or stalling healthy sibling
    assert pod._subscribers[str(tenant_a)] == {ws_healthy}


@pytest.mark.asyncio
async def test_domain_outbox_producers_stage_events_atomically() -> None:
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    dep_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. persist_deployment_idempotent (production FAILED + SUCCESS vs staging)
    added_objs: list[Any] = []

    class _DepFakeSession:
        def add(self, obj: Any) -> None:
            if isinstance(obj, Deployment) and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            added_objs.append(obj)

        async def flush(self) -> None:
            return None

        async def execute(self, _stmt: Any) -> _ScalarResult:
            return _ScalarResult([])

    dep_db = _DepFakeSession()
    await persist_deployment_idempotent(
        db=dep_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=None,
        integration_id=None,
        commit_sha="a" * 40,
        environment="production",
        outcome=DeploymentOutcome.FAILED,
        deployed_at=now,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        external_deployment_id=None,
        actor=None,
        actor_default_provider="direct",
    )
    await persist_deployment_idempotent(
        db=dep_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=None,
        integration_id=None,
        commit_sha="b" * 40,
        environment="production",
        outcome=DeploymentOutcome.SUCCESS,
        deployed_at=now,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        external_deployment_id=None,
        actor=None,
        actor_default_provider="direct",
    )
    await persist_deployment_idempotent(
        db=dep_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=None,
        integration_id=None,
        commit_sha="c" * 40,
        environment="staging",
        outcome=DeploymentOutcome.FAILED,
        deployed_at=now,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        external_deployment_id=None,
        actor=None,
        actor_default_provider="direct",
    )

    dep_events = [o for o in added_objs if isinstance(o, OutboxEvent)]
    assert [e.event_type for e in dep_events] == ["deployment.failed", "deployment.succeeded"]

    # 2. process_health_signal (FAIL -> incident.opened; PASS -> deployment.recovered)
    project = Project(
        id=project_id,
        tenant_id=tenant_id,
        name="Svc",
        slug="svc",
        health_status="HEALTHY",
        is_active=True,
        deleted_at=None,
        health_probe_source="webhook",
    )
    health_added: list[Any] = []
    open_failures: list[ProductionFailure] = []

    class _HealthFakeSession:
        def __init__(self) -> None:
            self._call_idx = 0

        def add(self, obj: Any) -> None:
            health_added.append(obj)
            if isinstance(obj, ProductionFailure):
                open_failures.append(obj)

        async def flush(self) -> None:
            return None

        async def execute(self, stmt: Any) -> _ScalarResult:
            entities = [d.get("entity") for d in stmt.column_descriptions]
            target = entities[0] if entities else None
            if target is Project:
                return _ScalarResult([project])
            if target is ProductionFailure:
                active = [f for f in open_failures if f.status == "OPEN"]
                return _ScalarResult(active)
            return _ScalarResult([])

    h_db = _HealthFakeSession()
    await process_health_signal(
        h_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        status=HealthSignalStatus.FAIL,
        signal_timestamp=now,
    )
    # Duplicate FAIL must NOT emit another outbox event
    await process_health_signal(
        h_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        status=HealthSignalStatus.FAIL,
        signal_timestamp=now + timedelta(seconds=10),
    )
    await process_health_signal(
        h_db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        project_id=project_id,
        status=HealthSignalStatus.PASS,
        signal_timestamp=now + timedelta(seconds=60),
    )

    health_events = [o for o in health_added if isinstance(o, OutboxEvent)]
    assert [e.event_type for e in health_events] == ["incident.opened", "deployment.recovered"]

    # 3. correlate_incident -> incident.linked
    issue = Issue(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        external_key="INC-101",
        title="Production outage",
        issue_type="Incident",
        status="Open",
        created_at=now,
    )
    deployment = Deployment(
        id=dep_id,
        tenant_id=tenant_id,
        project_id=project_id,
        environment="production",
        commit_sha="d" * 40,
        status="SUCCESS",
        is_change_failure=False,
        authoritative_signal_source="direct_deployment_api",
        deployed_at=now - timedelta(hours=1),
    )
    jira_added: list[Any] = []

    class _JiraFakeSession:
        def add(self, obj: Any) -> None:
            if isinstance(obj, DeploymentIncident) and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            jira_added.append(obj)

        async def flush(self) -> None:
            return None

        async def execute(self, stmt: Any) -> _ScalarResult:
            entities = [d.get("entity") for d in stmt.column_descriptions]
            target = entities[0] if entities else None
            if target is Deployment:
                return _ScalarResult([deployment])
            return _ScalarResult([])

    link = await correlate_incident(_JiraFakeSession(), issue)  # type: ignore[arg-type]
    assert link is not None
    jira_events = [o for o in jira_added if isinstance(o, OutboxEvent)]
    assert [e.event_type for e in jira_events] == ["incident.linked"]
    assert jira_events[0].payload["data"]["issue_key"] == "INC-101"
