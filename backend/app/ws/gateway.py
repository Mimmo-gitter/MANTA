"""Multi-pod Redis Streams fanout gateway for authenticated WebSocket clients (TASK-303-2)."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from contextlib import suppress
import json
import logging
import time
from typing import Any
import uuid
from weakref import WeakKeyDictionary

from fastapi import WebSocket
from opentelemetry.trace import SpanKind
import redis.asyncio as aioredis

from app.config import settings
from app.observability.tracing import (
    extract_trace_id_from_traceparent,
    get_current_trace_id_hex,
    start_linked_span,
)
from app.outbox.relay import build_stream_key

logger = logging.getLogger("app.ws.gateway")


def _decode_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_fields(raw_fields: Mapping[Any, Any]) -> dict[str, str]:
    return {_decode_str(k): _decode_str(v) for k, v in raw_fields.items()}


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
    ) -> None:
        self._redis_override = redis_client
        self._loop_redis_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            WeakKeyDictionary()
        )
        self.xread_block_ms = xread_block_ms
        self.batch_count = batch_count
        self.send_timeout_seconds = send_timeout_seconds
        self.dedup_cache_size = dedup_cache_size

        self._subscribers: dict[str, set[Any]] = defaultdict(set)
        self._cursors: dict[str, str] = {}
        self._reader_tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._recent_event_ids: dict[str, OrderedDict[str, None]] = defaultdict(OrderedDict)

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

    async def unregister_subscriber(
        self,
        tenant_id: uuid.UUID | str,
        websocket: WebSocket | Any,
    ) -> None:
        """Unregister a WebSocket client and stop the tenant reader when no local sockets remain."""
        tenant_key = str(tenant_id)
        task_to_cancel: asyncio.Task[None] | None = None
        async with self._get_lock(tenant_key):
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

    async def _broadcast_entry(
        self,
        tenant_key: str,
        stream_key: str,
        entry_id: Any,
        raw_fields: Mapping[Any, Any],
    ) -> bool:
        """Parse, deduplicate, trace, and concurrently send a single stream entry."""
        event_id, frame, traceparent, tracestate = build_ws_event_frame(
            tenant_key,
            entry_id,
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
            await asyncio.gather(
                *(self._safe_send(tenant_key, ws, raw_json) for ws in targets),
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
