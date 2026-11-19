"""
SCRUM-422 (Task 24.1) & SCRUM-423 (Task 24.2):
Canonical Provider Version Extraction & Conditional PostgreSQL Update Guards.

Features:
- Extracts deterministic monotonic BigInteger `entity_version` per provider:
  * GitHub: `pull_request.updated_at` parsed to UTC epoch milliseconds.
  * Jira: `changelog.id` parsed as positive integer, falling back to `issue.fields.updated` UTC epoch ms.
  * Slack: `event_ts` (or `ts` / `thread_ts`) parsed via Decimal to UTC epoch microseconds.
- Provides uniform conditional SQL update guard (`WHERE entity_version < :incoming_version`)
  for `pull_requests`, `issues`, and `communication_events`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import re
from typing import Any, Literal, Optional
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import CommunicationEvent, Issue, PullRequest
from app.webhooks.metrics import (
    DROPPED_STALE_EVENT_TOTAL,
    SUPPORTED_STALE_EVENT_PROVIDERS,
    TABLE_TO_STALE_EVENT_PROVIDER,
    dropped_stale_event_total,
    get_dropped_stale_event_count,
    record_dropped_stale_event,
)

logger = logging.getLogger("app.webhooks.versioning")

_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")
_NUMERIC_EPOCH_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

VersionedTableName = Literal["pull_requests", "issues", "communication_events"]

_VERSIONED_MODELS: dict[VersionedTableName, type[Any]] = {
    "pull_requests": PullRequest,
    "issues": Issue,
    "communication_events": CommunicationEvent,
}


@dataclass(frozen=True)
class VersionedMutationResult:
    """Outcome of a conditional `WHERE entity_version < :incoming_version` update."""

    applied: bool
    stale_rejected: bool
    entity_id: Optional[uuid.UUID]
    incoming_version: int


def parse_utc_datetime(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string, epoch number, or datetime into timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Auto-detect milliseconds vs seconds
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OverflowError):
            return None
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    else:
        raw = _TZ_NO_COLON.sub(r"\1:\2", raw)

    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def datetime_to_epoch_ms(dt: datetime) -> int:
    """Convert a datetime to exact UTC epoch milliseconds using integer arithmetic."""
    utc_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    utc_dt = utc_dt.astimezone(timezone.utc)
    delta = utc_dt - _UTC_EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1000 + (delta.microseconds // 1000)


def parse_timestamp_to_epoch_ms(value: Any) -> Optional[int]:
    """Parse an ISO-8601 timestamp, datetime, or numeric epoch value into positive UTC epoch ms."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        ms = datetime_to_epoch_ms(value)
        return ms if ms > 0 else None
    if isinstance(value, (int, float, Decimal)):
        try:
            dec = Decimal(str(value))
        except InvalidOperation:
            return None
        if not dec.is_finite() or dec <= 0:
            return None
        if dec < Decimal("1000000000000"):
            return int((dec * Decimal("1000")).to_integral_value())
        return int(dec.to_integral_value())
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if _NUMERIC_EPOCH_RE.match(raw):
            try:
                dec = Decimal(raw)
                if not dec.is_finite() or dec <= 0:
                    return None
                if dec < Decimal("1000000000000"):
                    return int((dec * Decimal("1000")).to_integral_value())
                return int(dec.to_integral_value())
            except InvalidOperation:
                return None
    dt = parse_utc_datetime(value)
    if dt is None:
        return None
    ms = datetime_to_epoch_ms(dt)
    return ms if ms > 0 else None


def parse_slack_ts_to_micros(value: Any) -> Optional[int]:
    """
    Convert a Slack timestamp string (e.g. '1731326400.123456') into exact UTC
    epoch microseconds using Decimal arithmetic (avoiding IEEE-754 float precision loss).
    """
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dec = Decimal(raw)
        if not dec.is_finite() or dec <= 0:
            return None
        return int((dec * Decimal("1000000")).to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _parse_positive_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def extract_github_pr_version(payload: Mapping[str, Any]) -> Optional[int]:
    """
    SCRUM-422 / SCRUM-424 Rule (GitHub PR):
    Extracts monotonic version from `pull_request.updated_at` parsed to UTC epoch ms,
    falling back through `merged_at`, `closed_at`, `created_at`, or explicit `entity_version`.
    """
    if not isinstance(payload, Mapping):
        return None
    pr_dict = payload.get("pull_request")
    pr_map: Mapping[str, Any] = pr_dict if isinstance(pr_dict, Mapping) else {}

    for candidate in (
        pr_map.get("updated_at"),
        payload.get("updated_at"),
        pr_map.get("merged_at"),
        pr_map.get("closed_at"),
        pr_map.get("created_at"),
    ):
        epoch_ms = parse_timestamp_to_epoch_ms(candidate)
        if epoch_ms is not None:
            return epoch_ms

    return _parse_positive_int(pr_map.get("entity_version") or payload.get("entity_version"))


def extract_jira_issue_version(payload: Mapping[str, Any]) -> Optional[int]:
    """
    SCRUM-422 / SCRUM-424 Rule (Jira Issue):
    Extracts `changelog.id` parsed as positive BigInteger, falling back to
    `issue.fields.updated` (or payload timestamp / created) in UTC epoch milliseconds.
    """
    if not isinstance(payload, Mapping):
        return None
    changelog = payload.get("changelog")
    if isinstance(changelog, Mapping):
        changelog_ver = _parse_positive_int(changelog.get("id"))
        if changelog_ver is not None:
            return changelog_ver

    direct_changelog = _parse_positive_int(payload.get("changelog_id"))
    if direct_changelog is not None:
        return direct_changelog

    issue_dict = payload.get("issue")
    issue_map: Mapping[str, Any] = issue_dict if isinstance(issue_dict, Mapping) else {}
    fields = issue_map.get("fields")
    fields_map: Mapping[str, Any] = fields if isinstance(fields, Mapping) else {}

    for candidate in (
        fields_map.get("updated"),
        payload.get("timestamp"),
        fields_map.get("created"),
    ):
        epoch_ms = parse_timestamp_to_epoch_ms(candidate)
        if epoch_ms is not None:
            return epoch_ms

    return _parse_positive_int(payload.get("entity_version"))


def extract_slack_event_version(payload: Mapping[str, Any]) -> Optional[int]:
    """
    SCRUM-422 / SCRUM-424 Rule (Slack Event):
    Extracts `event.event_ts` (or `payload.event_ts` / `event.ts` / `event.message.edited.ts` /
    `event.message.ts` / `event.thread_ts`) parsed via Decimal into UTC epoch microseconds.
    Falls back to `event_time * 1_000_000`.
    """
    if not isinstance(payload, Mapping):
        return None
    event_dict = payload.get("event")
    event_map: Mapping[str, Any] = event_dict if isinstance(event_dict, Mapping) else {}
    msg_dict = event_map.get("message")
    msg_map: Mapping[str, Any] = msg_dict if isinstance(msg_dict, Mapping) else {}
    edited_dict = msg_map.get("edited")
    edited_map: Mapping[str, Any] = edited_dict if isinstance(edited_dict, Mapping) else {}

    for candidate in (
        event_map.get("event_ts"),
        payload.get("event_ts"),
        event_map.get("ts"),
        payload.get("ts"),
        edited_map.get("ts"),
        msg_map.get("ts"),
        event_map.get("thread_ts"),
        payload.get("thread_ts"),
    ):
        micros = parse_slack_ts_to_micros(candidate)
        if micros is not None:
            return micros

    event_time = _parse_positive_int(payload.get("event_time"))
    if event_time is not None:
        return event_time * 1_000_000

    return _parse_positive_int(payload.get("entity_version"))


_PROVIDER_EXTRACTORS: dict[str, Callable[[Mapping[str, Any]], Optional[int]]] = {
    "github": extract_github_pr_version,
    "jira": extract_jira_issue_version,
    "slack": extract_slack_event_version,
}


def extract_entity_version(provider: str, payload: Mapping[str, Any]) -> Optional[int]:
    """Uniform dispatcher extracting canonical `entity_version` for supported providers."""
    clean_provider = (provider or "").strip().lower()
    extractor = _PROVIDER_EXTRACTORS.get(clean_provider)
    if extractor is None:
        raise ValueError(f"Unsupported provider for version extraction: {provider!r}")
    return extractor(payload)


async def execute_versioned_update(
    db: AsyncSession,
    *,
    table_name: VersionedTableName,
    record_id: uuid.UUID,
    tenant_id: uuid.UUID,
    incoming_version: int,
    values: Mapping[str, Any],
) -> VersionedMutationResult:
    """
    SCRUM-423: Executes a conditional PostgreSQL mutation:
      UPDATE <table> SET ..., entity_version = :incoming_version
      WHERE id = :record_id AND tenant_id = :tenant_id AND entity_version < :incoming_version
      RETURNING id

    Returns VersionedMutationResult indicating whether the row was updated (`applied=True`)
    or rejected due to stale/equal version (`applied=False, stale_rejected=True`).
    """
    model = _VERSIONED_MODELS.get(table_name)
    if model is None:
        raise ValueError(f"Unsupported versioned table: {table_name!r}")

    update_values = dict(values)
    update_values["entity_version"] = incoming_version

    stmt = (
        update(model)
        .where(
            model.id == record_id,
            model.tenant_id == tenant_id,
            model.entity_version < incoming_version,
        )
        .values(**update_values)
        .returning(model.id)
    )
    res = await db.execute(stmt)
    updated_id = res.scalar_one_or_none()

    if updated_id is not None:
        return VersionedMutationResult(
            applied=True,
            stale_rejected=False,
            entity_id=updated_id,
            incoming_version=incoming_version,
        )

    provider = TABLE_TO_STALE_EVENT_PROVIDER[table_name]
    record_dropped_stale_event(provider)
    logger.warning(
        "STALE_OUT_OF_ORDER_REJECTED provider=%s table=%s record_id=%s tenant_id=%s incoming_version=%s",
        provider,
        table_name,
        record_id,
        tenant_id,
        incoming_version,
    )
    return VersionedMutationResult(
        applied=False,
        stale_rejected=True,
        entity_id=record_id,
        incoming_version=incoming_version,
    )


__all__ = [
    "DROPPED_STALE_EVENT_TOTAL",
    "SUPPORTED_STALE_EVENT_PROVIDERS",
    "VersionedMutationResult",
    "VersionedTableName",
    "datetime_to_epoch_ms",
    "dropped_stale_event_total",
    "execute_versioned_update",
    "extract_entity_version",
    "extract_github_pr_version",
    "extract_jira_issue_version",
    "extract_slack_event_version",
    "get_dropped_stale_event_count",
    "parse_slack_ts_to_micros",
    "parse_timestamp_to_epoch_ms",
    "parse_utc_datetime",
    "record_dropped_stale_event",
]
