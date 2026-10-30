"""
SCRUM-335: Provider-specific Delivery Identifier Extraction and Normalization.

Supports:
- GitHub: Header `X-GitHub-Delivery` (UUID format), with payload fallback.
- Jira: Header `X-Atlassian-Webhook-Identifier` or payload event identifier.
- Slack: Payload `event_id` (e.g. `Ev024BE92L`) or `X-Slack-Request-Timestamp`.
- Deterministic fallback: Computes SHA-256 digest when providers omit an explicit ID.
"""

import hashlib
import json
import logging
from typing import Any, Mapping

logger = logging.getLogger("app.webhooks.delivery")


class DeliveryExtractionError(Exception):
    """Raised when delivery identifier cannot be extracted and explicit validation is required."""


def _get_header(headers: Mapping[str, str], header_name: str) -> str | None:
    """Case-insensitive header lookup."""
    target = header_name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            val = str(v).strip()
            if val:
                return val
    return None


def extract_delivery_id(
    provider: str,
    headers: Mapping[str, str],
    payload: dict[str, Any] | str | bytes | None = None,
    raw_body: bytes | None = None,
) -> str:
    """
    Extracts the unique delivery/event identifier for a webhook based on the provider.
    If no explicit provider ID is present, generates a deterministic SHA-256 fallback.
    """
    prov = (provider or "").strip().lower()

    # Parse payload if passed as bytes/string
    parsed_payload: dict[str, Any] = {}
    if isinstance(payload, dict):
        parsed_payload = payload
    elif isinstance(payload, (str, bytes)) and payload:
        try:
            parsed_payload = json.loads(payload)
        except Exception:
            parsed_payload = {}

    # 1. GitHub Extraction
    # Official spec: 'X-GitHub-Delivery' contains a unique GUID per delivery attempt
    if prov == "github":
        delivery_hdr = _get_header(headers, "X-GitHub-Delivery")
        if delivery_hdr:
            return delivery_hdr

        # Check payload fallback
        if "delivery_id" in parsed_payload:
            return str(parsed_payload["delivery_id"]).strip()
        if "id" in parsed_payload and "repository" in parsed_payload:
            return f"gh-{parsed_payload['id']}"

    # 2. Jira Extraction
    # Atlassian webhooks provide 'X-Atlassian-Webhook-Identifier' header or payload event/timestamp
    elif prov == "jira":
        jira_hdr = (
            _get_header(headers, "X-Atlassian-Webhook-Identifier")
            or _get_header(headers, "X-Atlassian-Token")
            or _get_header(headers, "X-Request-Id")
        )
        if jira_hdr:
            return jira_hdr

        # Payload extraction: Jira Cloud events typically include 'webhookEvent' and 'timestamp'
        webhook_event = parsed_payload.get("webhookEvent")
        ts = parsed_payload.get("timestamp")
        if webhook_event and ts:
            return f"jira-{webhook_event}-{ts}"

        # Alternative: issue ID + changelog ID / timestamp
        issue_id = (
            parsed_payload.get("issue", {}).get("id")
            if isinstance(parsed_payload.get("issue"), dict)
            else None
        )
        if issue_id and ts:
            return f"jira-issue-{issue_id}-{ts}"

        if "eventId" in parsed_payload:
            return str(parsed_payload["eventId"]).strip()

    # 3. Slack Extraction
    # Slack Events API payloads contain 'event_id' (e.g. 'Ev024BE92L')
    elif prov == "slack":
        slack_event_id = parsed_payload.get("event_id")
        if slack_event_id:
            return str(slack_event_id).strip()

        # Alternative Slack headers: request timestamp + retry count
        ts_hdr = _get_header(headers, "X-Slack-Request-Timestamp")
        retry_hdr = _get_header(headers, "X-Slack-Retry-Num") or "0"
        if ts_hdr:
            return f"slack-{ts_hdr}-r{retry_hdr}"

    # 4. Generic Provider Header Checks
    generic_hdr = (
        _get_header(headers, "X-Delivery-Id")
        or _get_header(headers, "X-Request-Id")
        or _get_header(headers, "X-Event-Id")
    )
    if generic_hdr:
        return generic_hdr

    if "delivery_id" in parsed_payload:
        return str(parsed_payload["delivery_id"]).strip()
    if "event_id" in parsed_payload:
        return str(parsed_payload["event_id"]).strip()

    # 5. Deterministic fallback for events missing explicit ID
    # Computes SHA-256 hash over raw body or serialized payload
    logger.debug(
        f"Provider '{prov}' did not provide explicit delivery ID. Computing deterministic fallback."
    )
    if raw_body:
        body_bytes = raw_body
    elif parsed_payload:
        body_bytes = json.dumps(parsed_payload, sort_keys=True).encode("utf-8")
    else:
        body_bytes = b"empty-body"

    hash_digest = hashlib.sha256(body_bytes).hexdigest()[:24]
    return f"gen-{prov}-{hash_digest}"
