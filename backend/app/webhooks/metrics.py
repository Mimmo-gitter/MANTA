"""
SCRUM-424 (Task 24.3): Prometheus Observability Metrics for Out-of-Order Webhook Handling.

Registers:
- `dropped_stale_event_total{provider="github"|"jira"|"slack"}`:
  Monotonic Prometheus Counter incremented by 1 whenever a conditional SQL update
  or version precedence guard drops a stale out-of-order webhook (0 rows mutated).
"""

from __future__ import annotations

from typing import Any, Literal, cast

try:
    from prometheus_client import Counter
except ImportError:  # pragma: no cover - optional fallback in minimal test envs
    Counter = None  # type: ignore[assignment,misc]

StaleEventProvider = Literal["github", "jira", "slack"]

SUPPORTED_STALE_EVENT_PROVIDERS: frozenset[str] = frozenset({"github", "jira", "slack"})
_ORDERED_STALE_EVENT_PROVIDERS: tuple[StaleEventProvider, ...] = ("github", "jira", "slack")

TABLE_TO_STALE_EVENT_PROVIDER: dict[str, StaleEventProvider] = {
    "pull_requests": "github",
    "issues": "jira",
    "communication_events": "slack",
}

if Counter is not None:
    # Note: prometheus_client strips the trailing `_total` from `_name` internally and
    # exposes the metric sample as `dropped_stale_event_total{provider="..."}`.
    DROPPED_STALE_EVENT_TOTAL: Any = Counter(
        "dropped_stale_event_total",
        "Total number of stale out-of-order webhook events dropped after 0-row stale updates.",
        labelnames=("provider",),
    )
    # Pre-initialize all allowed provider label series at 0.0 so PromQL rate()/increase()
    # and REGISTRY.get_sample_value() observe every series from process start.
    for _prov in _ORDERED_STALE_EVENT_PROVIDERS:
        DROPPED_STALE_EVENT_TOTAL.labels(provider=_prov)
else:  # pragma: no cover
    DROPPED_STALE_EVENT_TOTAL = None

# Convenience alias matching the exact Prometheus metric identifier referenced in SCRUM-424
dropped_stale_event_total = DROPPED_STALE_EVENT_TOTAL


def normalize_stale_event_provider(provider: str) -> StaleEventProvider:
    """Validate and canonicalize a provider label against {'github', 'jira', 'slack'}."""
    clean = (provider or "").strip().lower()
    if clean not in SUPPORTED_STALE_EVENT_PROVIDERS:
        raise ValueError(
            f"Unsupported provider for dropped_stale_event_total: {provider!r} "
            f"(expected one of {sorted(SUPPORTED_STALE_EVENT_PROVIDERS)})"
        )
    return cast(StaleEventProvider, clean)


def record_dropped_stale_event(provider: str, *, amount: int = 1) -> float:
    """
    Increment `dropped_stale_event_total{provider=<provider>}` by `amount` (default 1).

    Raises:
        ValueError: If `provider` is not in {'github', 'jira', 'slack'} or `amount <= 0`.
    """
    clean_provider = normalize_stale_event_provider(provider)
    if isinstance(amount, bool) or amount <= 0:
        raise ValueError(f"amount must be a positive integer, got {amount!r}")

    if DROPPED_STALE_EVENT_TOTAL is not None:
        child = DROPPED_STALE_EVENT_TOTAL.labels(provider=clean_provider)
        child.inc(amount)
        return float(child._value.get())
    return 0.0


def get_dropped_stale_event_count(provider: str) -> float:
    """Read the current `dropped_stale_event_total{provider=<provider>}` counter value."""
    clean_provider = normalize_stale_event_provider(provider)
    if DROPPED_STALE_EVENT_TOTAL is not None:
        return float(DROPPED_STALE_EVENT_TOTAL.labels(provider=clean_provider)._value.get())
    return 0.0


__all__ = [
    "DROPPED_STALE_EVENT_TOTAL",
    "SUPPORTED_STALE_EVENT_PROVIDERS",
    "StaleEventProvider",
    "TABLE_TO_STALE_EVENT_PROVIDER",
    "dropped_stale_event_total",
    "get_dropped_stale_event_count",
    "normalize_stale_event_provider",
    "record_dropped_stale_event",
]
