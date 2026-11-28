"""Multi-pod Redis Streams fanout & monotonic last_seq replay gateway (TASK-303-2 & TASK-305-1)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict, deque
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, Literal
import uuid
from weakref import WeakKeyDictionary

from fastapi import WebSocket
from opentelemetry.trace import SpanKind
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from app.config import settings
from app.observability.tracing import (
    extract_trace_id_from_traceparent,
    get_current_trace_id_hex,
    start_linked_span,
)
from app.outbox.relay import DEFAULT_STREAM_MAXLEN, build_stream_key

logger = logging.getLogger("app.ws.gateway")

DEFAULT_REPLAY_BUFFER_SIZE = DEFAULT_STREAM_MAXLEN  # 5,000 events per tenant stream
MAX_STREAM_ID_COMPONENT = (1 << 64) - 1
_STREAM_ID_RE = re.compile(r"^([0-9]{1,20})-([0-9]{1,20})$")


def _decode_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_fields(raw_fields: Mapping[Any, Any]) -> dict[str, str]:
    return {_decode_str(k): _decode_str(v) for k, v in raw_fields.items()}


def parse_stream_seq(raw_seq: str | bytes | None) -> tuple[str, tuple[int, int]] | None:
    """
    Validate and normalize a Redis Stream sequence ID (`<ms>-<seq>` or `"0"` -> `"0-0"`).

    Rejects Redis range/special tokens (`-`, `+`, `$`, `(id`, whitespace) and out-of-range
    uint64 components before any `XRANGE` call to prevent query injection or runtime errors.
    """
    if raw_seq is None:
        return None
    text = _decode_str(raw_seq).strip()
    if not text:
        return None
    if text == "0":
        return "0-0", (0, 0)
    match = _STREAM_ID_RE.match(text)
    if match is None:
        return None
    ms_part = int(match.group(1))
    seq_part = int(match.group(2))
    if ms_part > MAX_STREAM_ID_COMPONENT or seq_part > MAX_STREAM_ID_COMPONENT:
        return None
    return f"{ms_part}-{seq_part}", (ms_part, seq_part)


def _get_xinfo_field(info: Mapping[Any, Any], field_name: str) -> Any:
    if field_name in info:
        return info[field_name]
    b_key = field_name.encode("utf-8")
    if b_key in info:
        return info[b_key]
    return None


def build_replay_gap_frame(
    *,
    tenant_id: uuid.UUID | str,
    requested_last_seq: str,
    reason: str,
    earliest_available_seq: str | None,
    latest_seq: str,
    buffer_size: int,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Construct the canonical `REPLAY_GAP` WebSocket handshake/control JSON frame."""
    resolved_trace_id = trace_id if trace_id is not None else get_current_trace_id_hex()
    return {
        "type": "REPLAY_GAP",
        "code": "REPLAY_GAP",
        "reason": reason,
        "tenant_id": str(tenant_id),
        "requested_last_seq": requested_last_seq,
        "earliest_available_seq": earliest_available_seq,
        "latest_seq": latest_seq,
        "buffer_size": buffer_size,
        "action": "RESYNC_DASHBOARD_STATE",
        "resync_endpoint": "/api/v1/projects/{project_id}/dashboard-state",
        "trace_id": resolved_trace_id,
    }


@dataclass(frozen=True, slots=True)
class ReplayPlan:
    """Result of inspecting `tenant_stream:{tenant_id}` for a requested `last_seq` cursor."""

    outcome: Literal["replayed", "replay_gap"]
    entries: list[tuple[str, Mapping[Any, Any]]]
    high_water_seq: str
    gap_reason: str | None = None
    earliest_available_seq: str | None = None
    latest_seq: str = "0-0"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Outcome reported after completing replay/gap handshake and live stream handover."""

    outcome: Literal["live_only", "replayed", "replay_gap"]
    replayed_count: int = 0
    high_water_seq: str = "0-0"
    gap_reason: str | None = None
    earliest_available_seq: str | None = None
    latest_seq: str = "0-0"


@dataclass(slots=True)
class _SubscriberReplayBuffer:
    """Bounded per-subscriber staging buffer used while replay frames drain to the socket."""

    pending: deque[tuple[str, str | None, str]]
    max_pending: int
    overflowed: bool = False

    def append(self, seq: str, event_id: str | None, raw_json: str) -> None:
        if len(self.pending) >= self.max_pending:
            self.overflowed = True
            return
        self.pending.append((seq, event_id, raw_json))


def build_ws_event_frame(
    tenant_id: uuid.UUID | str,
    entry_id: str | bytes,
    raw_fields: Mapping[Any, Any],
) -> tuple[str | None, dict[str, Any], str | None, str | None]:
    """
    Transform a Redis Stream `tenant_stream:{tenant_id}` entry into the canonical
    outbound WebSocket JSON frame and extract W3C trace propagation headers.

    Returns `(event_id, frame_dict, traceparent, tracestate)`.
    """
    entry_id_str = _decode_str(entry_id)
    fields = _decode_fields(raw_fields)

    raw_payload = fields.get("payload", "{}")
    try:
        parsed_payload = json.loads(raw_payload) if raw_payload else {}
    except (json.JSONDecodeError, TypeError):
        parsed_payload = {"raw": raw_payload}
    if not isinstance(parsed_payload, dict):
        parsed_payload = {"value": parsed_payload}

    event_id = fields.get("event_id") or parsed_payload.get("event_id")
    if event_id is not None:
        event_id = str(event_id)

    event_type = str(fields.get("event_type") or parsed_payload.get("event_type") or "unknown")
    schema_version = str(parsed_payload.get("schema_version") or "1.0")
    frame_tenant_id = str(parsed_payload.get("tenant_id") or fields.get("tenant_id") or tenant_id)
    project_id = parsed_payload.get("project_id")
    if project_id is not None:
        project_id = str(project_id)

    occurred_at = parsed_payload.get("occurred_at") or fields.get("occurred_at")
    data = parsed_payload.get("data")
    if data is None:
        # Handle producers writing flat payload dicts directly into `outbox_events.payload`
        data = {
            k: v
            for k, v in parsed_payload.items()
            if k
            not in {
                "event_id",
                "event_type",
                "schema_version",
                "tenant_id",
                "project_id",
                "occurred_at",
                "traceparent",
                "tracestate",
            }
        }

    traceparent = fields.get("traceparent") or parsed_payload.get("traceparent")
    tracestate = fields.get("tracestate") or parsed_payload.get("tracestate")
    if not isinstance(traceparent, str) or not traceparent.strip():
        traceparent = None
    if not isinstance(tracestate, str) or not tracestate.strip():
        tracestate = None

    trace_id = extract_trace_id_from_traceparent(traceparent) or get_current_trace_id_hex()

    frame: dict[str, Any] = {
        "type": "event",
        "seq": entry_id_str,
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": schema_version,
        "tenant_id": frame_tenant_id,
        "project_id": project_id,
        "occurred_at": occurred_at,
        "data": data,
        "trace_id": trace_id,
    }
    return event_id, frame, traceparent, tracestate


class WebSocketGatewayManager:
    """
    Pod-local WebSocket subscriber manager consuming per-tenant Redis Streams via stateless
    `XREAD` so every backend pod receives and broadcasts ordered events to its local clients.
    """

    def __init__(
        self,
        redis_client: Any = None,
        *,
        xread_block_ms: int = 500,
        batch_count: int = 100,
        send_timeout_seconds: float = 1.5,
        dedup_cache_size: int = 5000,
        replay_buffer_size: int | None = None,
    ) -> None:
        resolved_replay_size = (
            replay_buffer_size
            if replay_buffer_size is not None
            else int(getattr(settings, "WS_REPLAY_BUFFER_SIZE", DEFAULT_REPLAY_BUFFER_SIZE))
        )
        if resolved_replay_size <= 0:
            raise ValueError("replay_buffer_size must be a positive integer")

        self._redis_override = redis_client
        self._loop_redis_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            WeakKeyDictionary()
        )
        self.xread_block_ms = xread_block_ms
        self.batch_count = batch_count
        self.send_timeout_seconds = send_timeout_seconds
        self.dedup_cache_size = dedup_cache_size
        self.replay_buffer_size = resolved_replay_size

        self._subscribers: dict[str, set[Any]] = defaultdict(set)
        self._cursors: dict[str, str] = {}
        self._reader_tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._recent_event_ids: dict[str, OrderedDict[str, None]] = defaultdict(OrderedDict)
        self._replay_buffers: dict[Any, _SubscriberReplayBuffer] = {}

    def set_redis_client(self, client: Any) -> None:
        """Override the Redis client used by this gateway instance (primarily for tests)."""
        self._redis_override = client

    def _get_lock(self, tenant_key: str) -> asyncio.Lock:
        lock = self._locks.get(tenant_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[tenant_key] = lock
        return lock

    def _get_redis(self) -> Any:
        if self._redis_override is not None:
            return self._redis_override
        loop = asyncio.get_running_loop()
        client = self._loop_redis_clients.get(loop)
        if client is None:
            client = aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
                health_check_interval=15,
            )
            self._loop_redis_clients[loop] = client
        return client

    def subscriber_count(self, tenant_id: uuid.UUID | str) -> int:
        """Return the number of active local subscribers for `tenant_id`."""
        return len(self._subscribers.get(str(tenant_id), ()))

    def _is_duplicate_event(self, tenant_key: str, event_id: str | None) -> bool:
        if not event_id:
            return False
        ring = self._recent_event_ids[tenant_key]
        if event_id in ring:
            return True
        ring[event_id] = None
        while len(ring) > self.dedup_cache_size:
            ring.popitem(last=False)
        return False

    async def snapshot_stream_tail(self, tenant_id: uuid.UUID | str) -> str:
        """
        Capture the current stream tail entry ID synchronously before `websocket.accept()`
        so events `XADD`-ed immediately after handshake are never missed by `XREAD ... $`.
        """
        stream_key = build_stream_key(str(tenant_id))
        client = self._get_redis()
        try:
            xrevrange_fn = getattr(client, "xrevrange", None)
            if callable(xrevrange_fn):
                entries = await xrevrange_fn(stream_key, "+", "-", count=1)
                if entries:
                    return _decode_str(entries[0][0])

            xrange_fn = getattr(client, "xrange", None)
            if callable(xrange_fn):
                entries = await xrange_fn(stream_key, "-", "+")
                if entries:
                    return _decode_str(entries[-1][0])
        except Exception as exc:
            logger.debug("Stream tail snapshot fallback to 0-0 for %s: %s", stream_key, exc)
        return "0-0"

    async def _fetch_stream_bounds(
        self,
        client: Any,
        stream_key: str,
    ) -> tuple[str | None, str | None]:
        """Return `(earliest_seq, latest_seq)` for `stream_key`, or `(None, None)` when empty."""
        earliest: str | None = None
        latest: str | None = None
        try:
            xrange_fn = getattr(client, "xrange", None)
            if callable(xrange_fn):
                first_entries = await xrange_fn(stream_key, "-", "+", count=1)
                if first_entries:
                    earliest = _decode_str(first_entries[0][0])

            xrevrange_fn = getattr(client, "xrevrange", None)
            if callable(xrevrange_fn):
                last_entries = await xrevrange_fn(stream_key, "+", "-", count=1)
                if last_entries:
                    latest = _decode_str(last_entries[0][0])
            elif earliest is not None and callable(xrange_fn):
                all_entries = await xrange_fn(stream_key, "-", "+")
                if all_entries:
                    latest = _decode_str(all_entries[-1][0])
        except Exception as exc:
            logger.debug("Failed to inspect stream bounds for %s: %s", stream_key, exc)
        return earliest, latest

    async def plan_replay(
        self,
        tenant_id: uuid.UUID | str,
        last_seq: str,
    ) -> ReplayPlan:
        """
        Evaluate whether `last_seq` falls inside the tenant's 5,000-event replay buffer.

        Uses inclusive `XRANGE stream_key last_seq + COUNT (buffer_size + 2)` so anchor
        presence (`entries[0][0] == normalized_seq`) and ordered missed events (`entries[1:]`)
        are determined atomically in one Redis command.
        """
        tenant_key = str(tenant_id)
        stream_key = build_stream_key(tenant_key)
        client = self._get_redis()

        parsed = parse_stream_seq(last_seq)
        if parsed is None:
            earliest, latest = await self._fetch_stream_bounds(client, stream_key)
            return ReplayPlan(
                outcome="replay_gap",
                entries=[],
                high_water_seq=latest or "0-0",
                gap_reason="invalid_last_seq",
                earliest_available_seq=earliest,
                latest_seq=latest or "0-0",
            )

        normalized_seq, seq_tuple = parsed

        if normalized_seq == "0-0":
            xinfo_fn = getattr(client, "xinfo_stream", None)
            if callable(xinfo_fn):
                try:
                    info = await xinfo_fn(stream_key)
                except ResponseError as exc:
                    if "no such key" in str(exc).lower():
                        return ReplayPlan(
                            outcome="replayed",
                            entries=[],
                            high_water_seq="0-0",
                            earliest_available_seq=None,
                            latest_seq="0-0",
                        )
                    raise
                except NotImplementedError:
                    info = None

                if isinstance(info, Mapping):
                    length = int(_get_xinfo_field(info, "length") or 0)
                    raw_added = _get_xinfo_field(info, "entries-added")
                    entries_added = int(raw_added) if raw_added is not None else None
                    raw_max_deleted = _get_xinfo_field(info, "max-deleted-entry-id")
                    max_deleted = (
                        _decode_str(raw_max_deleted) if raw_max_deleted is not None else None
                    )
                    max_deleted_parsed = parse_stream_seq(max_deleted)
                    has_evicted = (entries_added is not None and entries_added > length) or (
                        max_deleted_parsed is not None and max_deleted_parsed[1] > (0, 0)
                    )

                    if length == 0:
                        if has_evicted:
                            return ReplayPlan(
                                outcome="replay_gap",
                                entries=[],
                                high_water_seq="0-0",
                                gap_reason="sequence_evicted",
                                earliest_available_seq=None,
                                latest_seq="0-0",
                            )
                        return ReplayPlan(
                            outcome="replayed",
                            entries=[],
                            high_water_seq="0-0",
                            earliest_available_seq=None,
                            latest_seq="0-0",
                        )

                    if has_evicted:
                        earliest, latest = await self._fetch_stream_bounds(client, stream_key)
                        return ReplayPlan(
                            outcome="replay_gap",
                            entries=[],
                            high_water_seq=latest or "0-0",
                            gap_reason="sequence_evicted",
                            earliest_available_seq=earliest,
                            latest_seq=latest or "0-0",
                        )

                    if length > self.replay_buffer_size:
                        earliest, latest = await self._fetch_stream_bounds(client, stream_key)
                        return ReplayPlan(
                            outcome="replay_gap",
                            entries=[],
                            high_water_seq=latest or "0-0",
                            gap_reason="replay_limit_exceeded",
                            earliest_available_seq=earliest,
                            latest_seq=latest or "0-0",
                        )

            raw_entries = await client.xrange(
                stream_key,
                "-",
                "+",
                count=self.replay_buffer_size + 1,
            )
            if not raw_entries:
                return ReplayPlan(
                    outcome="replayed",
                    entries=[],
                    high_water_seq="0-0",
                    earliest_available_seq=None,
                    latest_seq="0-0",
                )
            if len(raw_entries) > self.replay_buffer_size:
                earliest = _decode_str(raw_entries[0][0])
                _, latest = await self._fetch_stream_bounds(client, stream_key)
                return ReplayPlan(
                    outcome="replay_gap",
                    entries=[],
                    high_water_seq=latest or _decode_str(raw_entries[-1][0]),
                    gap_reason="replay_limit_exceeded",
                    earliest_available_seq=earliest,
                    latest_seq=latest or _decode_str(raw_entries[-1][0]),
                )
            decoded_entries = [(_decode_str(eid), flds) for eid, flds in raw_entries]
            return ReplayPlan(
                outcome="replayed",
                entries=decoded_entries,
                high_water_seq=decoded_entries[-1][0],
                earliest_available_seq=decoded_entries[0][0],
                latest_seq=decoded_entries[-1][0],
            )

        # Non-zero last_seq: atomic anchor + missed entries query
        raw_entries = await client.xrange(
            stream_key,
            normalized_seq,
            "+",
            count=self.replay_buffer_size + 2,
        )
        if not raw_entries:
            earliest, latest = await self._fetch_stream_bounds(client, stream_key)
            if latest is None:
                gap_reason = "stream_reset"
            else:
                latest_parsed = parse_stream_seq(latest)
                if latest_parsed is not None and seq_tuple > latest_parsed[1]:
                    gap_reason = "cursor_ahead_of_stream"
                else:
                    gap_reason = "sequence_evicted"
            return ReplayPlan(
                outcome="replay_gap",
                entries=[],
                high_water_seq=latest or "0-0",
                gap_reason=gap_reason,
                earliest_available_seq=earliest,
                latest_seq=latest or "0-0",
            )

        first_id = _decode_str(raw_entries[0][0])
        if first_id != normalized_seq:
            _, latest = await self._fetch_stream_bounds(client, stream_key)
            return ReplayPlan(
                outcome="replay_gap",
                entries=[],
                high_water_seq=latest or first_id,
                gap_reason="sequence_evicted",
                earliest_available_seq=first_id,
                latest_seq=latest or first_id,
            )

        missed = [(_decode_str(eid), flds) for eid, flds in raw_entries[1:]]
        if len(missed) > self.replay_buffer_size:
            _, latest = await self._fetch_stream_bounds(client, stream_key)
            return ReplayPlan(
                outcome="replay_gap",
                entries=[],
                high_water_seq=latest or missed[-1][0],
                gap_reason="replay_limit_exceeded",
                earliest_available_seq=first_id,
                latest_seq=latest or missed[-1][0],
            )

        high_water = missed[-1][0] if missed else normalized_seq
        return ReplayPlan(
            outcome="replayed",
            entries=missed,
            high_water_seq=high_water,
            earliest_available_seq=first_id,
            latest_seq=high_water,
        )

    async def register_subscriber(
        self,
        tenant_id: uuid.UUID | str,
        websocket: WebSocket | Any,
        *,
        initial_cursor: str | None = None,
    ) -> None:
        """Register a connected WebSocket client and start the pod-local tenant reader."""
        tenant_key = str(tenant_id)
        async with self._get_lock(tenant_key):
            self._subscribers[tenant_key].add(websocket)
            if tenant_key not in self._cursors:
                self._cursors[tenant_key] = (
                    initial_cursor
                    if initial_cursor is not None
                    else await self.snapshot_stream_tail(tenant_key)
                )
            existing_task = self._reader_tasks.get(tenant_key)
            if existing_task is None or existing_task.done():
                self._reader_tasks[tenant_key] = asyncio.create_task(
                    self._tenant_reader_loop(tenant_key),
                    name=f"ws-fanout-{tenant_key}",
                )

    async def _send_direct(self, tenant_key: str, websocket: Any, raw_json: str) -> bool:
        """Send JSON frame during replay/handover, evicting stalled or disconnected sockets."""
        try:
            await asyncio.wait_for(
                websocket.send_text(raw_json),
                timeout=self.send_timeout_seconds,
            )
            return True
        except Exception as exc:
            logger.debug(
                "Evicting stalled/disconnected WebSocket during replay for tenant %s: %s",
                tenant_key,
                exc,
            )
            subs = self._subscribers.get(tenant_key)
            if subs is not None:
                subs.discard(websocket)
            self._replay_buffers.pop(websocket, None)
            return False

    async def _execute_replay_for_subscriber(
        self,
        tenant_key: str,
        websocket: WebSocket | Any,
        buf: _SubscriberReplayBuffer,
        *,
        last_seq: str,
    ) -> ReplayResult:
        """Execute replay or emit `REPLAY_GAP` and atomically drain buffered live frames."""
        stream_key = build_stream_key(tenant_key)
        try:
            with start_linked_span(
                "websocket.replay",
                kind=SpanKind.SERVER,
                attributes={
                    "messaging.system": "redis",
                    "messaging.destination.name": stream_key,
                    "manta.tenant_id": tenant_key,
                    "manta.ws.requested_last_seq": str(last_seq),
                    "manta.ws.replay_buffer_size": self.replay_buffer_size,
                },
            ) as span:
                plan = await self.plan_replay(tenant_key, last_seq)
                span.set_attribute("manta.ws.replay_outcome", plan.outcome)
                span.set_attribute("manta.ws.replayed_count", len(plan.entries))
                if plan.gap_reason:
                    span.set_attribute("manta.ws.replay_gap_reason", plan.gap_reason)

                if plan.outcome == "replay_gap":
                    gap_frame = build_replay_gap_frame(
                        tenant_id=tenant_key,
                        requested_last_seq=str(last_seq),
                        reason=plan.gap_reason or "sequence_evicted",
                        earliest_available_seq=plan.earliest_available_seq,
                        latest_seq=plan.latest_seq,
                        buffer_size=self.replay_buffer_size,
                        trace_id=get_current_trace_id_hex(),
                    )
                    gap_json = json.dumps(gap_frame, separators=(",", ":"), default=str)
                    sent = await self._send_direct(tenant_key, websocket, gap_json)
                    async with self._get_lock(tenant_key):
                        buf.pending.clear()
                        self._replay_buffers.pop(websocket, None)
                    if not sent:
                        return ReplayResult(
                            outcome="replay_gap",
                            replayed_count=0,
                            high_water_seq=plan.high_water_seq,
                            gap_reason=plan.gap_reason,
                            earliest_available_seq=plan.earliest_available_seq,
                            latest_seq=plan.latest_seq,
                        )
                    return ReplayResult(
                        outcome="replay_gap",
                        replayed_count=0,
                        high_water_seq=plan.high_water_seq,
                        gap_reason=plan.gap_reason,
                        earliest_available_seq=plan.earliest_available_seq,
                        latest_seq=plan.latest_seq,
                    )

                # Ordered replay path
                replayed_count = 0
                replayed_event_ids: set[str] = set()
                high_water_parsed = parse_stream_seq(plan.high_water_seq)
                high_water_tuple = high_water_parsed[1] if high_water_parsed else (0, 0)

                for entry_id_str, raw_fields in plan.entries:
                    event_id, frame, _tp, _ts = build_ws_event_frame(
                        tenant_key,
                        entry_id_str,
                        raw_fields,
                    )
                    if event_id and event_id in replayed_event_ids:
                        continue
                    if event_id:
                        replayed_event_ids.add(event_id)
                    if frame["trace_id"] is None:
                        frame["trace_id"] = get_current_trace_id_hex()
                    raw_json = json.dumps(frame, separators=(",", ":"), default=str)
                    if not await self._send_direct(tenant_key, websocket, raw_json):
                        return ReplayResult(
                            outcome="replayed",
                            replayed_count=replayed_count,
                            high_water_seq=plan.high_water_seq,
                            earliest_available_seq=plan.earliest_available_seq,
                            latest_seq=plan.latest_seq,
                        )
                    replayed_count += 1

                # Drain buffered live events strictly newer than high_water_seq under lock handover
                while True:
                    async with self._get_lock(tenant_key):
                        if websocket not in self._subscribers.get(tenant_key, ()):
                            self._replay_buffers.pop(websocket, None)
                            return ReplayResult(
                                outcome="replayed",
                                replayed_count=replayed_count,
                                high_water_seq=plan.high_water_seq,
                                earliest_available_seq=plan.earliest_available_seq,
                                latest_seq=plan.latest_seq,
                            )
                        if buf.overflowed:
                            buf.pending.clear()
                            self._replay_buffers.pop(websocket, None)
                            overflow_gap = True
                            break
                        if not buf.pending:
                            self._replay_buffers.pop(websocket, None)
                            overflow_gap = False
                            break
                        batch = list(buf.pending)
                        buf.pending.clear()

                    for buffered_seq, buffered_ev_id, buffered_json in batch:
                        seq_parsed = parse_stream_seq(buffered_seq)
                        if seq_parsed is not None and seq_parsed[1] <= high_water_tuple:
                            continue
                        if buffered_ev_id and buffered_ev_id in replayed_event_ids:
                            continue
                        if buffered_ev_id:
                            replayed_event_ids.add(buffered_ev_id)
                        if not await self._send_direct(tenant_key, websocket, buffered_json):
                            return ReplayResult(
                                outcome="replayed",
                                replayed_count=replayed_count,
                                high_water_seq=plan.high_water_seq,
                                earliest_available_seq=plan.earliest_available_seq,
                                latest_seq=plan.latest_seq,
                            )
                        replayed_count += 1
                        if seq_parsed is not None and seq_parsed[1] > high_water_tuple:
                            high_water_tuple = seq_parsed[1]

                if overflow_gap:
                    earliest, latest = await self._fetch_stream_bounds(
                        self._get_redis(), stream_key
                    )
                    gap_frame = build_replay_gap_frame(
                        tenant_id=tenant_key,
                        requested_last_seq=str(last_seq),
                        reason="replay_limit_exceeded",
                        earliest_available_seq=earliest or plan.earliest_available_seq,
                        latest_seq=latest or plan.latest_seq,
                        buffer_size=self.replay_buffer_size,
                        trace_id=get_current_trace_id_hex(),
                    )
                    await self._send_direct(
                        tenant_key,
                        websocket,
                        json.dumps(gap_frame, separators=(",", ":"), default=str),
                    )
                    return ReplayResult(
                        outcome="replay_gap",
                        replayed_count=replayed_count,
                        high_water_seq=latest or plan.high_water_seq,
                        gap_reason="replay_limit_exceeded",
                        earliest_available_seq=earliest or plan.earliest_available_seq,
                        latest_seq=latest or plan.latest_seq,
                    )

                return ReplayResult(
                    outcome="replayed",
                    replayed_count=replayed_count,
                    high_water_seq=f"{high_water_tuple[0]}-{high_water_tuple[1]}",
                    earliest_available_seq=plan.earliest_available_seq,
                    latest_seq=plan.latest_seq,
                )
        finally:
            self._replay_buffers.pop(websocket, None)

    async def replay_and_register_subscriber(
        self,
        tenant_id: uuid.UUID | str,
        websocket: WebSocket | Any,
        *,
        last_seq: str | None = None,
        initial_cursor: str | None = None,
    ) -> ReplayResult:
        """
        Register a WebSocket subscriber and optionally replay missed events from `last_seq`.

        When `last_seq` is None, registers for live fanout only without emitting any handshake
        frame. When `last_seq` is supplied, atomically buffers live fanout during `XRANGE`
        replay and transitions seamlessly to live delivery (or emits `REPLAY_GAP`).
        """
        if last_seq is None or not str(last_seq).strip():
            await self.register_subscriber(
                tenant_id,
                websocket,
                initial_cursor=initial_cursor,
            )
            return ReplayResult(outcome="live_only")

        tenant_key = str(tenant_id)
        buf = _SubscriberReplayBuffer(
            pending=deque(),
            max_pending=self.replay_buffer_size,
        )
        async with self._get_lock(tenant_key):
            self._subscribers[tenant_key].add(websocket)
            self._replay_buffers[websocket] = buf
            if tenant_key not in self._cursors:
                self._cursors[tenant_key] = (
                    initial_cursor
                    if initial_cursor is not None
                    else await self.snapshot_stream_tail(tenant_key)
                )
            existing_task = self._reader_tasks.get(tenant_key)
            if existing_task is None or existing_task.done():
                self._reader_tasks[tenant_key] = asyncio.create_task(
                    self._tenant_reader_loop(tenant_key),
                    name=f"ws-fanout-{tenant_key}",
                )

        return await self._execute_replay_for_subscriber(
            tenant_key,
            websocket,
            buf,
            last_seq=str(last_seq).strip(),
        )

    async def resume_subscriber(
        self,
        tenant_id: uuid.UUID | str,
        websocket: WebSocket | Any,
        *,
        last_seq: str,
    ) -> ReplayResult:
        """Handle an in-band `{"type": "resume", "last_seq": "..."}` client request."""
        tenant_key = str(tenant_id)
        buf = _SubscriberReplayBuffer(
            pending=deque(),
            max_pending=self.replay_buffer_size,
        )
        async with self._get_lock(tenant_key):
            self._subscribers[tenant_key].add(websocket)
            self._replay_buffers[websocket] = buf

        return await self._execute_replay_for_subscriber(
            tenant_key,
            websocket,
            buf,
            last_seq=str(last_seq).strip(),
        )

    async def unregister_subscriber(
        self,
        tenant_id: uuid.UUID | str,
        websocket: WebSocket | Any,
    ) -> None:
        """Unregister a WebSocket client and stop the tenant reader when no local sockets remain."""
        tenant_key = str(tenant_id)
        task_to_cancel: asyncio.Task[None] | None = None
        async with self._get_lock(tenant_key):
            self._replay_buffers.pop(websocket, None)
            subs = self._subscribers.get(tenant_key)
            if subs is not None:
                subs.discard(websocket)
                if not subs:
                    self._subscribers.pop(tenant_key, None)
                    self._cursors.pop(tenant_key, None)
                    task_to_cancel = self._reader_tasks.pop(tenant_key, None)
        if task_to_cancel is not None and not task_to_cancel.done():
            task_to_cancel.cancel()
            with suppress(asyncio.CancelledError):
                await task_to_cancel

    async def _safe_send(self, tenant_key: str, websocket: Any, raw_json: str) -> None:
        """Send JSON frame with bounded timeout, evicting stalled or disconnected sockets."""
        try:
            await asyncio.wait_for(
                websocket.send_text(raw_json),
                timeout=self.send_timeout_seconds,
            )
        except Exception as exc:
            logger.debug(
                "Evicting stalled/disconnected WebSocket subscriber for tenant %s: %s",
                tenant_key,
                exc,
            )
            subs = self._subscribers.get(tenant_key)
            if subs is not None:
                subs.discard(websocket)
            self._replay_buffers.pop(websocket, None)

    async def _broadcast_entry(
        self,
        tenant_key: str,
        stream_key: str,
        entry_id: Any,
        raw_fields: Mapping[Any, Any],
    ) -> bool:
        """Parse, deduplicate, trace, and concurrently send a single stream entry."""
        entry_id_str = _decode_str(entry_id)
        event_id, frame, traceparent, tracestate = build_ws_event_frame(
            tenant_key,
            entry_id_str,
            raw_fields,
        )
        if self._is_duplicate_event(tenant_key, event_id):
            logger.debug(
                "Suppressed duplicate WebSocket broadcast event_id=%s tenant=%s",
                event_id,
                tenant_key,
            )
            return False

        targets = list(self._subscribers.get(tenant_key, ()))
        if not targets:
            return False

        with start_linked_span(
            "websocket.broadcast",
            traceparent=traceparent,
            tracestate=tracestate,
            kind=SpanKind.CONSUMER,
            attributes={
                "messaging.system": "redis",
                "messaging.destination.name": stream_key,
                "manta.tenant_id": tenant_key,
                "manta.event_type": str(frame["event_type"]),
                "manta.event_id": str(event_id or ""),
                "manta.stream_seq": str(frame["seq"]),
                "manta.subscriber_count": len(targets),
            },
        ):
            if frame["trace_id"] is None:
                frame["trace_id"] = get_current_trace_id_hex()
            raw_json = json.dumps(frame, separators=(",", ":"), default=str)

            live_targets: list[Any] = []
            for ws in targets:
                buf = self._replay_buffers.get(ws)
                if buf is not None:
                    buf.append(entry_id_str, event_id, raw_json)
                else:
                    live_targets.append(ws)

            if live_targets:
                await asyncio.gather(
                    *(self._safe_send(tenant_key, ws, raw_json) for ws in live_targets),
                    return_exceptions=False,
                )
        return True

    async def pump_tenant_once(
        self,
        tenant_id: uuid.UUID | str,
        *,
        block_ms: int | None = None,
    ) -> int:
        """
        Execute one non-destructive `XREAD` poll for `tenant_id` and fan out any new entries
        to all locally connected subscribers of that tenant.
        """
        tenant_key = str(tenant_id)
        targets = self._subscribers.get(tenant_key)
        if not targets:
            return 0

        cursor = self._cursors.get(tenant_key, "0-0")
        stream_key = build_stream_key(tenant_key)
        effective_block = self.xread_block_ms if block_ms is None else block_ms
        client = self._get_redis()

        batches = await client.xread(
            {stream_key: cursor},
            count=self.batch_count,
            block=effective_block,
        )
        if not batches:
            return 0

        delivered = 0
        for _raw_skey, entries in batches:
            for entry_id, raw_fields in entries:
                entry_id_str = _decode_str(entry_id)
                self._cursors[tenant_key] = entry_id_str
                if await self._broadcast_entry(tenant_key, stream_key, entry_id_str, raw_fields):
                    delivered += 1
        return delivered

    async def _tenant_reader_loop(self, tenant_key: str) -> None:
        """Background per-pod reader loop streaming ordered tenant events to local sockets."""
        while tenant_key in self._subscribers and self._subscribers[tenant_key]:
            started = time.monotonic()
            try:
                delivered = await self.pump_tenant_once(tenant_key)
                elapsed = time.monotonic() - started
                if delivered == 0 and elapsed < 0.01:
                    # Anti-busy-spin guard when in-memory test Redis stubs ignore `block=`
                    await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "WebSocket fanout reader error for tenant %s: %s",
                    tenant_key,
                    exc,
                )
                await asyncio.sleep(0.2)

    async def shutdown(self) -> None:
        """Cancel all active reader tasks and close gateway-managed Redis clients."""
        tasks = list(self._reader_tasks.values())
        self._reader_tasks.clear()
        self._subscribers.clear()
        self._cursors.clear()
        self._recent_event_ids.clear()
        self._replay_buffers.clear()

        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

        if self._redis_override is None:
            for client in list(self._loop_redis_clients.values()):
                aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
                if aclose is not None:
                    res = aclose()
                    if asyncio.iscoroutine(res):
                        with suppress(Exception):
                            await res
            self._loop_redis_clients.clear()
