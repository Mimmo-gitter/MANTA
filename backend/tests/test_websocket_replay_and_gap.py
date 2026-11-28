"""Automated test suite for TASK-305-1: Monotonic Redis Stream last_seq replay & REPLAY_GAP."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from typing import Any
from unittest import mock
import uuid

from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
import httpx
import pytest
import redis.asyncio as aioredis

from app.auth.jwks import JWKSClient
from app.core.uuidv7 import generate_uuidv7
from app.main import app
from app.outbox.relay import build_stream_key
from app.ws.gateway import (
    DEFAULT_REPLAY_BUFFER_SIZE,
    WebSocketGatewayManager,
    parse_stream_seq,
)
from tests.test_websocket_auth_and_fanout import (
    InMemoryStreamsRedis,
    _FakeWebSocket,
    _jwks_handler,
    _mint_jwt,
)


async def _append_tenant_events(
    redis_client: Any,
    tenant_id: uuid.UUID | str,
    count: int,
    *,
    maxlen: int | None = DEFAULT_REPLAY_BUFFER_SIZE,
    start_ordinal: int = 0,
    duplicate_event_id: str | None = None,
) -> list[tuple[str, str]]:
    """Publish `count` ordered events onto `tenant_stream:{tenant_id}` and return `(seq, event_id)`."""
    stream_key = build_stream_key(str(tenant_id))
    published: list[tuple[str, str]] = []
    for idx in range(count):
        ev_id = duplicate_event_id or str(generate_uuidv7())
        ordinal = start_ordinal + idx
        occurred = datetime.now(timezone.utc).isoformat()
        seq_id = await redis_client.xadd(
            stream_key,
            {
                "event_id": ev_id,
                "event_type": "deployment.failed" if ordinal % 2 == 0 else "incident.opened",
                "tenant_id": str(tenant_id),
                "occurred_at": occurred,
                "payload": json.dumps(
                    {
                        "event_id": ev_id,
                        "event_type": "deployment.failed"
                        if ordinal % 2 == 0
                        else "incident.opened",
                        "schema_version": "1.0",
                        "tenant_id": str(tenant_id),
                        "project_id": str(uuid.uuid4()),
                        "occurred_at": occurred,
                        "data": {"ordinal": ordinal},
                    }
                ),
            },
            maxlen=maxlen,
            approximate=False,
        )
        seq_str = seq_id.decode("utf-8") if isinstance(seq_id, bytes) else str(seq_id)
        published.append((seq_str, ev_id))
    return published


@pytest.fixture
def patched_replay_ws_app():
    orig_jwks = getattr(app.state, "jwks_client", None)
    orig_gateway = getattr(app.state, "ws_gateway", None)
    shared_redis = InMemoryStreamsRedis()

    app.state.jwks_client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(_jwks_handler),
    )
    app.state.ws_gateway = WebSocketGatewayManager(
        redis_client=shared_redis,
        xread_block_ms=10,
    )

    @asynccontextmanager
    async def _no_db_ctx(_tenant_uuid: Any):
        raise OSError("DB unavailable in unit mock; fallback to token claims")
        yield

    with mock.patch("app.auth.permissions.get_tenant_db_context", _no_db_ctx):
        yield app, shared_redis, app.state.ws_gateway

    app.state.jwks_client = orig_jwks
    app.state.ws_gateway = orig_gateway


# ==============================================================================
# Sequence ID Validation & Security Hardening
# ==============================================================================


@pytest.mark.parametrize(
    ("raw_seq", "expected"),
    [
        ("0", ("0-0", (0, 0))),
        ("0-0", ("0-0", (0, 0))),
        ("1795768000001-0", ("1795768000001-0", (1795768000001, 0))),
        (b"  1795768000001-42 \n", ("1795768000001-42", (1795768000001, 42))),
        ("", None),
        ("   ", None),
        (None, None),
        ("-", None),
        ("+", None),
        ("$", None),
        (">", None),
        ("(1795768000001-0", None),
        ("-1-0", None),
        ("1795768000001", None),
        ("1795768000001-0-1", None),
        ("abc-def", None),
        ("18446744073709551616-0", None),  # > uint64 max
    ],
)
def test_parse_stream_seq_validation_and_injection_guard(
    raw_seq: Any,
    expected: tuple[str, tuple[int, int]] | None,
) -> None:
    assert parse_stream_seq(raw_seq) == expected


# ==============================================================================
# TASK-305-1 Core Replay & REPLAY_GAP Handshake (FastAPI WebSocket Route)
# ==============================================================================


def test_ws_reconnect_with_valid_last_seq_replays_3_missed_events_in_order(
    patched_replay_ws_app: Any,
) -> None:
    """US-35 / TASK-305-1 AC: Reconnecting with valid S1 replays 3 missed events in exact order."""
    test_app, shared_redis, _gateway = patched_replay_ws_app
    tenant_a = uuid.uuid4()
    token = _mint_jwt(tenant_a, ["DEVELOPER"])
    client = TestClient(test_app)

    # Seed S1 plus 3 missed events (S2, S3, S4)
    published = asyncio.run(_append_tenant_events(shared_redis, tenant_a, 4))
    s1_seq = published[0][0]
    expected_seqs = [published[1][0], published[2][0], published[3][0]]
    expected_event_ids = [published[1][1], published[2][1], published[3][1]]

    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream?last_seq={s1_seq}",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        replayed = [ws.receive_json() for _ in range(3)]
        assert [f["type"] for f in replayed] == ["event", "event", "event"]
        assert [f["seq"] for f in replayed] == expected_seqs
        assert [f["event_id"] for f in replayed] == expected_event_ids
        assert [f["data"]["ordinal"] for f in replayed] == [1, 2, 3]

        # Connection stays open and transitions seamlessly to live ping/events
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


def test_ws_reconnect_with_evicted_last_seq_emits_replay_gap_immediately(
    patched_replay_ws_app: Any,
) -> None:
    """US-35 / TASK-305-1 AC: Evicted S0 outside 5,000-event buffer emits REPLAY_GAP immediately."""
    test_app, shared_redis, _gateway = patched_replay_ws_app
    tenant_a = uuid.uuid4()
    token = _mint_jwt(tenant_a, ["SCRUM_MASTER"])
    client = TestClient(test_app)

    # Publish 5,005 events with MAXLEN 5000 so the first 5 (including S0) are evicted
    published = asyncio.run(
        _append_tenant_events(
            shared_redis,
            tenant_a,
            DEFAULT_REPLAY_BUFFER_SIZE + 5,
            maxlen=DEFAULT_REPLAY_BUFFER_SIZE,
        )
    )
    evicted_s0 = published[0][0]
    earliest_retained = published[5][0]
    latest_seq = published[-1][0]

    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream?last_seq={evicted_s0}",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        frame = ws.receive_json()
        assert frame["type"] == "REPLAY_GAP"
        assert frame["code"] == "REPLAY_GAP"
        assert frame["reason"] == "sequence_evicted"
        assert frame["tenant_id"] == str(tenant_a)
        assert frame["requested_last_seq"] == evicted_s0
        assert frame["earliest_available_seq"] == earliest_retained
        assert frame["latest_seq"] == latest_seq
        assert frame["buffer_size"] == DEFAULT_REPLAY_BUFFER_SIZE
        assert frame["action"] == "RESYNC_DASHBOARD_STATE"
        assert frame["resync_endpoint"] == "/api/v1/projects/{project_id}/dashboard-state"

        # Socket remains usable after REPLAY_GAP
        ws.send_text('{"type":"ping"}')
        assert ws.receive_json() == {"type": "pong"}


def test_ws_reconnect_supports_last_event_id_header_and_in_band_resume(
    patched_replay_ws_app: Any,
) -> None:
    test_app, shared_redis, _gateway = patched_replay_ws_app
    tenant_a = uuid.uuid4()
    token = _mint_jwt(tenant_a, ["TENANT_ADMIN"])
    client = TestClient(test_app)

    published = asyncio.run(_append_tenant_events(shared_redis, tenant_a, 5))
    s1_seq = published[1][0]
    s3_seq = published[3][0]

    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream",
        headers={
            "Authorization": f"Bearer {token}",
            "Last-Event-ID": s1_seq,
        },
    ) as ws:
        frames = [ws.receive_json() for _ in range(3)]
        assert [f["data"]["ordinal"] for f in frames] == [2, 3, 4]

        # In-band resume request from s3_seq replays ordinal 4
        ws.send_text(json.dumps({"type": "resume", "last_seq": s3_seq}))
        resumed = ws.receive_json()
        assert resumed["type"] == "event"
        assert resumed["data"]["ordinal"] == 4


def test_ws_reconnect_invalid_and_cross_tenant_last_seq_guards(
    patched_replay_ws_app: Any,
) -> None:
    test_app, shared_redis, _gateway = patched_replay_ws_app
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    token_a = _mint_jwt(tenant_a, ["DEVELOPER"])
    token_b = _mint_jwt(tenant_b, ["DEVELOPER"])
    client = TestClient(test_app)

    published_a = asyncio.run(_append_tenant_events(shared_redis, tenant_a, 3))
    s0_a = published_a[0][0]

    # 1. Cross-tenant reconnect to Tenant A's stream using Tenant B token still fails pre-accept 1008
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/ws/tenants/{tenant_a}/stream?last_seq={s0_a}",
            headers={"Authorization": f"Bearer {token_b}"},
        ):
            pass
    assert exc.value.code == 1008

    # 2. Malformed Redis range token on own stream triggers structured REPLAY_GAP (never crashes)
    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_a}/stream?last_seq=-",
        headers={"Authorization": f"Bearer {token_a}"},
    ) as ws:
        gap = ws.receive_json()
        assert gap["type"] == "REPLAY_GAP"
        assert gap["reason"] == "invalid_last_seq"

    # 3. Tenant B passing Tenant A's sequence on Tenant B's empty stream gets REPLAY_GAP, zero A data
    with client.websocket_connect(
        f"/api/v1/ws/tenants/{tenant_b}/stream?last_seq={s0_a}",
        headers={"Authorization": f"Bearer {token_b}"},
    ) as ws:
        gap_b = ws.receive_json()
        assert gap_b["type"] == "REPLAY_GAP"
        assert gap_b["reason"] == "stream_reset"
        assert gap_b["tenant_id"] == str(tenant_b)


# ==============================================================================
# Gateway Unit Edge Cases: 5,000 Boundary, 0-0, Concurrency & Pod Isolation
# ==============================================================================


@pytest.mark.asyncio
async def test_gateway_exact_5000_buffer_boundary_and_limit_exceeded() -> None:
    shared_redis = InMemoryStreamsRedis()
    gateway = WebSocketGatewayManager(redis_client=shared_redis, replay_buffer_size=5_000)
    tenant_id = uuid.uuid4()

    # Keep 5,002 entries in stream (simulating approximate MAXLEN ~ 5000 retention)
    published = await _append_tenant_events(shared_redis, tenant_id, 5_002, maxlen=6_000)
    try:
        # 5,000 missed events from index 1 -> succeeds
        ws_ok = _FakeWebSocket()
        res_ok = await gateway.replay_and_register_subscriber(
            tenant_id, ws_ok, last_seq=published[1][0]
        )
        assert res_ok.outcome == "replayed"
        assert res_ok.replayed_count == 5_000
        assert len(ws_ok.sent_frames) == 5_000
        assert ws_ok.sent_frames[0]["seq"] == published[2][0]
        assert ws_ok.sent_frames[-1]["seq"] == published[-1][0]

        # 5,001 missed events from index 0 -> exceeds 5,000 buffer cap -> REPLAY_GAP
        ws_over = _FakeWebSocket()
        res_over = await gateway.replay_and_register_subscriber(
            tenant_id, ws_over, last_seq=published[0][0]
        )
        assert res_over.outcome == "replay_gap"
        assert res_over.gap_reason == "replay_limit_exceeded"
        assert len(ws_over.sent_frames) == 1
        assert ws_over.sent_frames[0]["type"] == "REPLAY_GAP"
        assert ws_over.sent_frames[0]["reason"] == "replay_limit_exceeded"
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_gateway_zero_seq_semantics_caught_up_and_cursor_ahead() -> None:
    shared_redis = InMemoryStreamsRedis()
    gateway = WebSocketGatewayManager(redis_client=shared_redis, replay_buffer_size=10)
    tenant_empty = uuid.uuid4()
    tenant_untrimmed = uuid.uuid4()
    tenant_trimmed = uuid.uuid4()

    try:
        # 1. "0-0" on brand-new empty stream -> 0 events, no REPLAY_GAP
        ws_empty = _FakeWebSocket()
        res_empty = await gateway.replay_and_register_subscriber(
            tenant_empty, ws_empty, last_seq="0-0"
        )
        assert res_empty.outcome == "replayed"
        assert res_empty.replayed_count == 0
        assert ws_empty.sent_frames == []

        # 2. "0-0" on untrimmed small stream -> replays all 3 events
        pubs_small = await _append_tenant_events(shared_redis, tenant_untrimmed, 3, maxlen=10)
        ws_small = _FakeWebSocket()
        res_small = await gateway.replay_and_register_subscriber(
            tenant_untrimmed, ws_small, last_seq="0-0"
        )
        assert res_small.outcome == "replayed"
        assert res_small.replayed_count == 3
        assert [f["seq"] for f in ws_small.sent_frames] == [p[0] for p in pubs_small]

        # 3. Caught-up at exact tail -> 0 replayed events, no REPLAY_GAP
        ws_tail = _FakeWebSocket()
        res_tail = await gateway.replay_and_register_subscriber(
            tenant_untrimmed, ws_tail, last_seq=pubs_small[-1][0]
        )
        assert res_tail.outcome == "replayed"
        assert res_tail.replayed_count == 0
        assert ws_tail.sent_frames == []

        # 4. Cursor ahead of stream -> REPLAY_GAP(cursor_ahead_of_stream)
        ws_ahead = _FakeWebSocket()
        res_ahead = await gateway.replay_and_register_subscriber(
            tenant_untrimmed, ws_ahead, last_seq="9999999999999-0"
        )
        assert res_ahead.outcome == "replay_gap"
        assert res_ahead.gap_reason == "cursor_ahead_of_stream"
        assert ws_ahead.sent_frames[0]["type"] == "REPLAY_GAP"

        # 5. "0-0" on trimmed stream -> REPLAY_GAP(sequence_evicted)
        await _append_tenant_events(shared_redis, tenant_trimmed, 15, maxlen=10)
        ws_trim = _FakeWebSocket()
        res_trim = await gateway.replay_and_register_subscriber(
            tenant_trimmed, ws_trim, last_seq="0-0"
        )
        assert res_trim.outcome == "replay_gap"
        assert res_trim.gap_reason == "sequence_evicted"
        assert ws_trim.sent_frames[0]["type"] == "REPLAY_GAP"
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_gateway_concurrent_live_handover_zero_gaps_zero_duplicates_and_sibling_isolation() -> (
    None
):
    """
    Verify:
    1. If Subscriber A receives S2 live on a pod, Subscriber B reconnecting with last_seq=S1
       still receives S2 during replay (pod-global dedup ring does NOT suppress replay).
    2. Outbox crash-retry duplicate event_id inside the stream IS deduplicated to 1 frame.
    3. A live event arriving concurrently during replay handover is delivered exactly once
       strictly after replayed events.
    """
    shared_redis = InMemoryStreamsRedis()
    gateway = WebSocketGatewayManager(redis_client=shared_redis, xread_block_ms=10)
    tenant_id = uuid.uuid4()

    try:
        # Attach Subscriber A manually (matching fanout unit tests) so the background reader loop
        # does not race manual pump_tenant_once() assertions below.
        ws_live_a = _FakeWebSocket()
        gateway._subscribers[str(tenant_id)].add(ws_live_a)
        gateway._cursors[str(tenant_id)] = "0-0"

        p1 = await _append_tenant_events(shared_redis, tenant_id, 1, start_ordinal=0)
        dup_id = str(generate_uuidv7())
        p2 = await _append_tenant_events(
            shared_redis, tenant_id, 2, start_ordinal=1, duplicate_event_id=dup_id
        )
        p3 = await _append_tenant_events(shared_redis, tenant_id, 1, start_ordinal=3)

        # Pump live to Subscriber A so `dup_id` and p3 enter gateway._recent_event_ids
        delivered = await gateway.pump_tenant_once(tenant_id, block_ms=0)
        assert delivered == 3
        assert [f["data"]["ordinal"] for f in ws_live_a.sent_frames] == [0, 1, 3]

        # Now Subscriber B reconnects from p1[0][0] while a concurrent live broadcast arrives
        ws_replay_b = _FakeWebSocket()
        orig_plan = gateway.plan_replay

        async def _plan_with_concurrent_live(tid: Any, seq: str) -> Any:
            plan = await orig_plan(tid, seq)
            # Inject a brand-new live event right after XRANGE completes, before drain finishes
            await _append_tenant_events(shared_redis, tenant_id, 1, start_ordinal=4)
            await gateway.pump_tenant_once(tenant_id, block_ms=0)
            return plan

        with mock.patch.object(gateway, "plan_replay", side_effect=_plan_with_concurrent_live):
            res_b = await gateway.replay_and_register_subscriber(
                tenant_id, ws_replay_b, last_seq=p1[0][0]
            )

        assert res_b.outcome == "replayed"
        assert [f["data"]["ordinal"] for f in ws_replay_b.sent_frames] == [1, 3, 4]
        assert ws_replay_b.sent_frames[0]["seq"] == p2[0][0]
        assert ws_replay_b.sent_frames[1]["seq"] == p3[0][0]

        # Subsequent live event flows normally to both A and B (either background reader or pump)
        await _append_tenant_events(shared_redis, tenant_id, 1, start_ordinal=5)
        await gateway.pump_tenant_once(tenant_id, block_ms=0)
        await asyncio.sleep(0.02)
        assert ws_replay_b.sent_frames[-1]["data"]["ordinal"] == 5
        assert ws_live_a.sent_frames[-1]["data"]["ordinal"] == 5
    finally:
        await gateway.shutdown()


# ==============================================================================
# Live Valkey 7 Integration Test (runs automatically in CI; skips if Valkey down)
# ==============================================================================


@pytest.mark.asyncio
async def test_live_valkey_stream_replay_and_trimmed_replay_gap() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    client = aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1.0)
    try:
        await client.ping()
        try:
            await client.xinfo_stream(f"__manta_streams_probe_{uuid.uuid4()}")
        except aioredis.ResponseError as exc:
            if "unknown command" in str(exc).lower():
                raise RuntimeError(f"Streams unsupported: {exc}") from exc
    except Exception as exc:
        await client.aclose()
        pytest.skip(f"Valkey/Redis with Streams not available at {redis_url}: {exc}")

    tenant_id = uuid.uuid4()
    stream_key = build_stream_key(tenant_id)
    gateway = WebSocketGatewayManager(redis_client=client, replay_buffer_size=5_000)

    try:
        published = await _append_tenant_events(client, tenant_id, 4, maxlen=5_000)
        ws_replay = _FakeWebSocket()
        res = await gateway.replay_and_register_subscriber(
            tenant_id, ws_replay, last_seq=published[0][0]
        )
        assert res.outcome == "replayed"
        assert res.replayed_count == 3
        assert [f["seq"] for f in ws_replay.sent_frames] == [p[0] for p in published[1:]]

        # Trim stream past published[0][0] and verify real Valkey REPLAY_GAP
        await client.xtrim(stream_key, maxlen=2, approximate=False)
        ws_gap = _FakeWebSocket()
        res_gap = await gateway.replay_and_register_subscriber(
            tenant_id, ws_gap, last_seq=published[0][0]
        )
        assert res_gap.outcome == "replay_gap"
        assert res_gap.gap_reason == "sequence_evicted"
        assert ws_gap.sent_frames[0]["type"] == "REPLAY_GAP"
        assert ws_gap.sent_frames[0]["earliest_available_seq"] == published[2][0]
        assert ws_gap.sent_frames[0]["latest_seq"] == published[3][0]
    finally:
        await gateway.shutdown()
        await client.delete(stream_key)
        await client.aclose()
