"""
TASK-302-1 (SCRUM-525 / US-49): Core Prometheus Observability Metrics.

Registers and updates on `GET /metrics` via `prometheus-client` and
`prometheus-fastapi-instrumentator`:
- `webhook_ack_latency_seconds{provider, status}` (Histogram):
  End-to-end HTTP acknowledgment latency for incoming `/api/v1/webhooks/*` requests.
- `arq_queue_depth{queue}` (Gauge):
  Live Valkey/Redis job depth across `default`, `high`, `delayed`, and aggregated `in_flight`.
- `redis_stream_consumer_lag{stream, consumer_group}` (Gauge):
  Live undelivered + pending (unacknowledged PEL) consumer lag across `tenant_stream:*`
  Redis Streams aggregated by logical stream family (`tenant_stream`) and sanitized group.

Security & Privacy Invariant:
- Zero PII, credentials, signatures, tenant UUIDs, integration/project IDs, or worker
  hostnames may ever appear in Prometheus metric labels.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
import logging
import re
from typing import Any, Optional
import weakref

from fastapi import FastAPI
from prometheus_client import Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator, metrics as instrumentator_metrics
import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError

from app.config import settings
from app.outbox.consumer import DEFAULT_GROUP_NAME
from app.outbox.relay import STREAM_KEY_PREFIX
from workers.retry import QUEUE_DELAYED
from workers.worker import IN_FLIGHT_PREFIX, QUEUE_DEFAULT, QUEUE_HIGH

logger = logging.getLogger("app.observability.metrics")

WEBHOOK_PATH_PREFIX = "/api/v1/webhooks"

# Fine-grained buckets centered on the <100ms (0.1s) p95 webhook ACK SLO (US-15)
WEBHOOK_ACK_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.15,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
)

_KNOWN_WEBHOOK_PROVIDERS: frozenset[str] = frozenset(
    {"github", "jira", "slack", "argocd", "generic", "health"}
)
ALLOWED_WEBHOOK_PROVIDERS: frozenset[str] = _KNOWN_WEBHOOK_PROVIDERS | frozenset({"unknown"})

_KNOWN_WEBHOOK_STATUSES: frozenset[str] = frozenset(
    {"200", "202", "400", "401", "403", "404", "422", "429", "500", "503"}
)
ALLOWED_WEBHOOK_STATUSES: frozenset[str] = _KNOWN_WEBHOOK_STATUSES | frozenset({"other"})

ALLOWED_ARQ_QUEUES: tuple[str, ...] = ("default", "high", "delayed", "in_flight")

_PREINIT_WEBHOOK_SERIES: tuple[tuple[str, str], ...] = (
    ("github", "202"),
    ("jira", "202"),
    ("slack", "202"),
    ("argocd", "202"),
    ("generic", "202"),
    ("health", "200"),
)

_SAFE_GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_LONG_HEX_OR_DIGITS_RE = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "api-key",
    "bearer",
    "auth",
    "ghp_",
    "gho_",
    "sk_",
    "xoxb-",
    "xoxp-",
    "eyj",
)

_REDIS_TRANSIENT_ERRORS = (RedisError, OSError, asyncio.TimeoutError, TimeoutError, ConnectionError)

# ------------------------------------------------------------------------------
# Prometheus Metric Definitions (registered on default prometheus_client.REGISTRY)
# ------------------------------------------------------------------------------

WEBHOOK_ACK_LATENCY_SECONDS = Histogram(
    "webhook_ack_latency_seconds",
    "End-to-end HTTP acknowledgment latency in seconds for incoming webhook requests.",
    labelnames=("provider", "status"),
    buckets=WEBHOOK_ACK_LATENCY_BUCKETS,
)
for _provider, _status in _PREINIT_WEBHOOK_SERIES:
    WEBHOOK_ACK_LATENCY_SECONDS.labels(provider=_provider, status=_status)

ARQ_QUEUE_DEPTH = Gauge(
    "arq_queue_depth",
    "Current number of jobs across Valkey/Redis ARQ queues.",
    labelnames=("queue",),
)
for _queue in ALLOWED_ARQ_QUEUES:
    ARQ_QUEUE_DEPTH.labels(queue=_queue).set(0.0)

REDIS_STREAM_CONSUMER_LAG = Gauge(
    "redis_stream_consumer_lag",
    "Number of undelivered and pending unacknowledged entries in Redis Streams "
    "per logical stream family and consumer group.",
    labelnames=("stream", "consumer_group"),
)
REDIS_STREAM_CONSUMER_LAG.labels(
    stream=STREAM_KEY_PREFIX,
    consumer_group=DEFAULT_GROUP_NAME,
).set(0.0)

# Convenience aliases matching the exact Prometheus metric names in TASK-302-1
webhook_ack_latency_seconds = WEBHOOK_ACK_LATENCY_SECONDS
arq_queue_depth = ARQ_QUEUE_DEPTH
redis_stream_consumer_lag = REDIS_STREAM_CONSUMER_LAG

# Tracks active (stream, consumer_group) label pairs so vanished groups reset cleanly to 0.0
_active_stream_group_labels: set[tuple[str, str]] = {(STREAM_KEY_PREFIX, DEFAULT_GROUP_NAME)}

# Loop-scoped Redis clients prevent cross-loop RuntimeError in function-scoped pytest-asyncio
# tests while reusing a single connection pool in production uvicorn workers.
_override_redis_client: Optional[Any] = None
_loop_redis_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis] = (
    weakref.WeakKeyDictionary()
)


# ------------------------------------------------------------------------------
# Strict Zero-PII / Zero-Secret Label Sanitizers
# ------------------------------------------------------------------------------


def _decode_str(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def normalize_webhook_provider(provider: Any) -> str:
    """Canonicalize a webhook provider strictly against the fixed allowlist."""
    if not isinstance(provider, (str, bytes, bytearray)):
        return "unknown"
    clean = _decode_str(provider).strip().lower()
    if clean in _KNOWN_WEBHOOK_PROVIDERS:
        return clean
    return "unknown"


def normalize_http_status(status_code: Any) -> str:
    """Canonicalize an HTTP status code strictly against the bounded status allowlist."""
    if status_code is None or isinstance(status_code, bool):
        return "other"
    clean = str(status_code).strip()
    if clean in _KNOWN_WEBHOOK_STATUSES:
        return clean
    return "other"


def normalize_stream_label(stream_key: Any) -> str:
    """Map a per-tenant Redis stream key (`tenant_stream:{uuid}`) to its logical stream family.

    Never emits tenant UUIDs or arbitrary key suffixes into Prometheus labels.
    """
    if not isinstance(stream_key, (str, bytes, bytearray)):
        return "other"
    raw = _decode_str(stream_key).strip()
    if raw == STREAM_KEY_PREFIX or raw.startswith(f"{STREAM_KEY_PREFIX}:"):
        return STREAM_KEY_PREFIX
    return "other"


def sanitize_consumer_group(group_name: Any) -> str:
    """Validate a Redis Stream consumer group name against PII/secret/cardinality rules.

    Rejects emails, UUIDs, high-entropy/long hex strings, secret-like tokens, and
    non-identifier characters by mapping them to `"other"`.
    """
    if not isinstance(group_name, (str, bytes, bytearray)):
        return "other"
    clean = _decode_str(group_name).strip().lower()
    if not clean or not _SAFE_GROUP_RE.match(clean):
        return "other"
    if _UUID_RE.search(clean) or _LONG_HEX_OR_DIGITS_RE.search(clean):
        return "other"
    if any(marker in clean for marker in _SENSITIVE_SUBSTRINGS):
        return "other"
    return clean


# ------------------------------------------------------------------------------
# Webhook ACK Latency Recording & Instrumentator Wiring
# ------------------------------------------------------------------------------


def observe_webhook_ack_latency(
    *,
    provider: Any,
    status_code: Any,
    duration_seconds: float,
) -> tuple[str, str, float]:
    """Observe a single webhook acknowledgment latency sample with sanitized labels."""
    clean_provider = normalize_webhook_provider(provider)
    clean_status = normalize_http_status(status_code)
    clamped_duration = max(0.0, float(duration_seconds))
    WEBHOOK_ACK_LATENCY_SECONDS.labels(
        provider=clean_provider,
        status=clean_status,
    ).observe(clamped_duration)
    return clean_provider, clean_status, clamped_duration


def _resolve_webhook_provider_from_info(info: instrumentator_metrics.Info) -> str:
    """Resolve the sanitized webhook provider from request state or route template."""
    state_provider = getattr(info.request.state, "webhook_provider", None)
    if state_provider is not None:
        return normalize_webhook_provider(state_provider)

    handler = info.modified_handler or ""
    if handler == f"{WEBHOOK_PATH_PREFIX}/projects/{{project_id}}/health":
        return "health"
    if handler == f"{WEBHOOK_PATH_PREFIX}/{{provider}}":
        path_params = getattr(info.request, "path_params", None) or {}
        return normalize_webhook_provider(path_params.get("provider"))
    return "unknown"


def webhook_ack_latency_instrumentation() -> Callable[[instrumentator_metrics.Info], None]:
    """Build a `prometheus-fastapi-instrumentator` callback for `webhook_ack_latency_seconds`."""

    def instrumentation(info: instrumentator_metrics.Info) -> None:
        handler = info.modified_handler or ""
        if not handler.startswith(WEBHOOK_PATH_PREFIX):
            return
        status_code = info.response.status_code if info.response is not None else 500
        provider = _resolve_webhook_provider_from_info(info)
        observe_webhook_ack_latency(
            provider=provider,
            status_code=status_code,
            duration_seconds=info.modified_duration,
        )

    return instrumentation


def setup_prometheus_instrumentation(app: FastAPI) -> Instrumentator:
    """Attach `prometheus-fastapi-instrumentator` with safe labels and webhook ACK timing.

    Must be invoked BEFORE `CORSMiddleware` is added in `app/main.py` so CORS remains
    the outermost middleware in Starlette's stack.
    """
    instrumentator = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        should_group_untemplated=True,
        should_respect_env_var=False,
        should_instrument_requests_inprogress=False,
        excluded_handlers=[r"^/metrics$", r"^/health$"],
    )
    instrumentator.add(instrumentator_metrics.default())
    instrumentator.add(webhook_ack_latency_instrumentation())
    instrumentator.instrument(app)
    app.state.prometheus_instrumentator = instrumentator
    return instrumentator


# ------------------------------------------------------------------------------
# Valkey / Redis Live Broker Metrics Collection
# ------------------------------------------------------------------------------


def set_metrics_redis(client: Optional[Any]) -> None:
    """Override or clear the Redis client used for `/metrics` live scrapes (DI / tests)."""
    global _override_redis_client
    _override_redis_client = client


def get_metrics_redis(redis_url: Optional[str] = None) -> Any:
    """Return the injected or event-loop-scoped async Redis client for metrics scrapes."""
    if _override_redis_client is not None:
        return _override_redis_client

    timeout = max(0.1, float(settings.METRICS_REDIS_TIMEOUT_SECONDS))
    url = redis_url or settings.REDIS_URL
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return aioredis.from_url(
            url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry_on_timeout=False,
        )

    client = _loop_redis_clients.get(loop)
    if client is None:
        client = aioredis.from_url(
            url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry_on_timeout=False,
            health_check_interval=15,
        )
        _loop_redis_clients[loop] = client
    return client


async def close_metrics_redis() -> None:
    """Close any internally managed metrics Redis clients (called on FastAPI shutdown)."""
    clients = list(_loop_redis_clients.values())
    _loop_redis_clients.clear()
    for client in clients:
        aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
        if callable(aclose):
            with suppress(Exception):
                res = aclose()
                if asyncio.iscoroutine(res):
                    await res


async def _discover_keys_bounded(redis: Any, pattern: str, *, limit: int) -> list[str]:
    """Discover Redis keys matching `pattern` using bounded SCAN (with KEYS fallback)."""
    discovered: list[str] = []
    seen: set[str] = set()

    scan_iter_fn = getattr(redis, "scan_iter", None)
    if callable(scan_iter_fn):
        iterator = scan_iter_fn(match=pattern)
        if hasattr(iterator, "__aiter__"):
            async for item in iterator:
                if isinstance(item, (str, bytes, bytearray)):
                    key = _decode_str(item)
                    if key not in seen:
                        seen.add(key)
                        discovered.append(key)
                        if len(discovered) >= limit:
                            break
            return discovered

    keys_fn = getattr(redis, "keys", None)
    if callable(keys_fn):
        raw_keys = await keys_fn(pattern)
        if isinstance(raw_keys, (list, tuple, set, frozenset)):
            for item in raw_keys:
                if isinstance(item, (str, bytes, bytearray)):
                    key = _decode_str(item)
                    if key not in seen:
                        seen.add(key)
                        discovered.append(key)
                        if len(discovered) >= limit:
                            break

    return discovered


def _get_group_field(group: Mapping[Any, Any], field_name: str) -> Any:
    """Read a field from an `XINFO GROUPS` dict whether keys are str or bytes."""
    if field_name in group:
        return group[field_name]
    b_key = field_name.encode("utf-8")
    if b_key in group:
        return group[b_key]
    return None


def _to_non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


async def collect_arq_queue_depths(
    redis: Any,
    *,
    scan_limit: Optional[int] = None,
) -> dict[str, int]:
    """Query live Valkey/Redis depths for `default`, `high`, `delayed`, and `in_flight`."""
    effective_limit = max(1, int(scan_limit or settings.METRICS_REDIS_SCAN_LIMIT))

    default_len, high_len, delayed_len, in_flight_keys = await asyncio.gather(
        redis.llen(QUEUE_DEFAULT),
        redis.llen(QUEUE_HIGH),
        redis.zcard(QUEUE_DELAYED),
        _discover_keys_bounded(redis, f"{IN_FLIGHT_PREFIX}*", limit=effective_limit),
    )

    in_flight_total = 0
    if in_flight_keys:
        in_flight_lengths = await asyncio.gather(*(redis.llen(k) for k in in_flight_keys))
        in_flight_total = sum(max(0, int(length or 0)) for length in in_flight_lengths)

    return {
        "default": max(0, int(default_len or 0)),
        "high": max(0, int(high_len or 0)),
        "delayed": max(0, int(delayed_len or 0)),
        "in_flight": in_flight_total,
    }


async def _compute_single_stream_lag(
    redis: Any,
    stream_key: str,
) -> list[tuple[str, str, int]]:
    """Return `(logical_stream, sanitized_group, total_lag)` tuples for one Redis Stream."""
    logical_stream = normalize_stream_label(stream_key)
    xinfo_groups_fn = getattr(redis, "xinfo_groups", None)
    xlen_fn = getattr(redis, "xlen", None)

    groups: list[Mapping[Any, Any]] = []
    if callable(xinfo_groups_fn):
        try:
            raw_groups = await xinfo_groups_fn(stream_key)
            if isinstance(raw_groups, (list, tuple)):
                groups = [g for g in raw_groups if isinstance(g, Mapping)]
        except ResponseError:
            # Key vanished between SCAN and XINFO or wrong type; ignore safely.
            return []

    if not groups:
        if callable(xlen_fn):
            try:
                stream_len = max(0, int(await xlen_fn(stream_key) or 0))
            except ResponseError:
                return []
            return [(logical_stream, DEFAULT_GROUP_NAME, stream_len)]
        return []

    results: list[tuple[str, str, int]] = []
    cached_xlen: int | None = None

    for group in groups:
        group_label = sanitize_consumer_group(_get_group_field(group, "name"))
        pending = _to_non_negative_int(_get_group_field(group, "pending")) or 0
        raw_lag = _to_non_negative_int(_get_group_field(group, "lag"))

        if raw_lag is not None:
            undelivered = raw_lag
        else:
            # Redis 7 returns lag=None if trimmed past last-delivered-id or entries-read unknown.
            if cached_xlen is None and callable(xlen_fn):
                with suppress(ResponseError):
                    cached_xlen = max(0, int(await xlen_fn(stream_key) or 0))
            entries_read = _to_non_negative_int(_get_group_field(group, "entries-read"))
            last_delivered_raw = _get_group_field(group, "last-delivered-id")
            last_delivered = (
                _decode_str(last_delivered_raw).strip() if last_delivered_raw is not None else ""
            )
            if cached_xlen is not None and entries_read is not None:
                undelivered = max(0, cached_xlen - entries_read)
            elif cached_xlen is not None and last_delivered in {"", "0-0", "0"}:
                undelivered = cached_xlen
            else:
                undelivered = 0

        results.append((logical_stream, group_label, undelivered + pending))

    return results


async def collect_redis_stream_consumer_lag(
    redis: Any,
    *,
    scan_limit: Optional[int] = None,
) -> dict[tuple[str, str], int]:
    """Query live total lag (undelivered + pending PEL) across all `tenant_stream:*` keys."""
    effective_limit = max(1, int(scan_limit or settings.METRICS_REDIS_SCAN_LIMIT))
    totals: dict[tuple[str, str], int] = {(STREAM_KEY_PREFIX, DEFAULT_GROUP_NAME): 0}

    stream_keys = await _discover_keys_bounded(
        redis,
        f"{STREAM_KEY_PREFIX}:*",
        limit=effective_limit,
    )
    if not stream_keys:
        return totals

    per_stream_results = await asyncio.gather(
        *(_compute_single_stream_lag(redis, skey) for skey in stream_keys)
    )
    for entries in per_stream_results:
        for logical_stream, group_label, lag_val in entries:
            key = (logical_stream, group_label)
            totals[key] = totals.get(key, 0) + max(0, int(lag_val))

    return totals


async def refresh_broker_metrics(
    *,
    redis_client: Optional[Any] = None,
    timeout_seconds: Optional[float] = None,
    scan_limit: Optional[int] = None,
) -> bool:
    """Refresh `arq_queue_depth` and `redis_stream_consumer_lag` gauges live from Valkey/Redis.

    Returns True on successful scrape, False when skipped or degraded due to Redis unavailability.
    Never raises so `GET /metrics` always responds HTTP 200 without leaking broker credentials.
    """
    if (
        not settings.METRICS_REDIS_ENABLED
        and redis_client is None
        and _override_redis_client is None
    ):
        return False

    client = redis_client if redis_client is not None else get_metrics_redis()
    timeout = max(0.1, float(timeout_seconds or settings.METRICS_REDIS_TIMEOUT_SECONDS))

    try:
        depths, lag_map = await asyncio.wait_for(
            asyncio.gather(
                collect_arq_queue_depths(client, scan_limit=scan_limit),
                collect_redis_stream_consumer_lag(client, scan_limit=scan_limit),
            ),
            timeout=timeout,
        )
    except _REDIS_TRANSIENT_ERRORS as exc:
        logger.debug(
            "Broker metrics scrape degraded (%s); retaining last gauge values.", type(exc).__name__
        )
        return False
    except Exception as exc:  # pragma: no cover - defensive fallback so /metrics never returns 500
        logger.warning("Unexpected error refreshing broker metrics (%s).", type(exc).__name__)
        return False

    for queue_name in ALLOWED_ARQ_QUEUES:
        ARQ_QUEUE_DEPTH.labels(queue=queue_name).set(float(depths.get(queue_name, 0)))

    # Reset any previously observed dynamic (stream, consumer_group) series that disappeared
    stale_pairs = _active_stream_group_labels - set(lag_map.keys())
    for stale_stream, stale_group in stale_pairs:
        REDIS_STREAM_CONSUMER_LAG.labels(
            stream=stale_stream,
            consumer_group=stale_group,
        ).set(0.0)

    for (logical_stream, group_label), lag_value in lag_map.items():
        REDIS_STREAM_CONSUMER_LAG.labels(
            stream=logical_stream,
            consumer_group=group_label,
        ).set(float(lag_value))

    _active_stream_group_labels.clear()
    _active_stream_group_labels.update(lag_map.keys())
    return True


__all__ = [
    "ALLOWED_ARQ_QUEUES",
    "ALLOWED_WEBHOOK_PROVIDERS",
    "ALLOWED_WEBHOOK_STATUSES",
    "ARQ_QUEUE_DEPTH",
    "REDIS_STREAM_CONSUMER_LAG",
    "WEBHOOK_ACK_LATENCY_BUCKETS",
    "WEBHOOK_ACK_LATENCY_SECONDS",
    "arq_queue_depth",
    "close_metrics_redis",
    "collect_arq_queue_depths",
    "collect_redis_stream_consumer_lag",
    "get_metrics_redis",
    "normalize_http_status",
    "normalize_stream_label",
    "normalize_webhook_provider",
    "observe_webhook_ack_latency",
    "redis_stream_consumer_lag",
    "refresh_broker_metrics",
    "sanitize_consumer_group",
    "set_metrics_redis",
    "setup_prometheus_instrumentation",
    "webhook_ack_latency_instrumentation",
    "webhook_ack_latency_seconds",
]

# Deferred side-effect import so `app.webhooks.router` (via `app.webhooks.__init__`) sees a
# fully initialized `app.observability.metrics` module when imported first through verification.
import app.webhooks.metrics  # noqa: E402, F401 - ensure dropped_stale_event_total is registered
