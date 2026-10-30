"""
SCRUM-325 & SCRUM-326: Webhook HMAC Signature Verification.

Provides constant-time signature verification for incoming webhooks:
- GitHub: HMAC-SHA256 over raw body via header `X-Hub-Signature-256` (`sha256=<hex>`).
- Jira: HMAC-SHA256 over raw body via `X-Hub-Signature-256` or `X-Atlassian-Webhook-Signature`.
- Slack: HMAC-SHA256 over `v0:{timestamp}:{raw_body}` via `X-Slack-Signature` (`v0=<hex>`),
         with strict 5-minute replay window validation via `X-Slack-Request-Timestamp`.

Security Invariants:
- All comparisons use constant-time `hmac.compare_digest` to prevent timing attacks.
- Verification executes over raw unparsed request bytes before JSON decoding.
- Replay attacks are rejected via timestamp freshness assertion.
"""

import hashlib
import hmac
import logging
import time
from typing import Mapping

from fastapi import HTTPException, status

logger = logging.getLogger("app.webhooks.verification")

SLACK_MAX_REPLAY_AGE_SECONDS = 300  # 5 minutes replay window (SCRUM-326)


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            val = str(v).strip()
            if val:
                return val
    return None


def verify_github_signature(
    secret: str,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> None:
    """
    SCRUM-325: Verifies GitHub webhook payload signature against X-Hub-Signature-256.
    Raises HTTPException(401) on failure.
    """
    signature_header = _get_header(headers, "X-Hub-Signature-256")
    if not signature_header:
        # Check legacy header fallback
        signature_header = _get_header(headers, "X-Hub-Signature")
        if not signature_header:
            logger.warning("GitHub webhook missing signature header (X-Hub-Signature-256)")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing GitHub webhook signature header",
                headers={"WWW-Authenticate": "WebhookSignature"},
            )

    computed_mac = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    expected_sig = f"sha256={computed_mac}"

    if signature_header.startswith("sha256="):
        valid = hmac.compare_digest(signature_header, expected_sig)
    else:
        valid = hmac.compare_digest(signature_header, computed_mac)

    if not valid:
        logger.warning("GitHub webhook signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub webhook signature",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )


def verify_jira_signature(
    secret: str,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> None:
    """
    SCRUM-325: Verifies Jira webhook HMAC-SHA256 signature.
    Raises HTTPException(401) on failure.
    """
    signature_header = (
        _get_header(headers, "X-Hub-Signature-256")
        or _get_header(headers, "X-Atlassian-Webhook-Signature")
        or _get_header(headers, "X-Hub-Signature")
    )
    if not signature_header:
        logger.warning("Jira webhook missing signature header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Jira webhook signature header",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    computed_mac = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    expected_sig = f"sha256={computed_mac}"
    valid = hmac.compare_digest(signature_header, expected_sig) or hmac.compare_digest(
        signature_header, computed_mac
    )

    if not valid:
        logger.warning("Jira webhook signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Jira webhook signature",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )


def verify_slack_signature(
    secret: str,
    raw_body: bytes,
    headers: Mapping[str, str],
    max_replay_age: int = SLACK_MAX_REPLAY_AGE_SECONDS,
) -> None:
    """
    SCRUM-326: Verifies Slack webhook signature and enforces 5-minute replay window.
    Raises HTTPException(401) on failure.
    """
    sig_header = _get_header(headers, "X-Slack-Signature")
    ts_header = _get_header(headers, "X-Slack-Request-Timestamp")

    if not sig_header or not ts_header:
        logger.warning("Slack webhook missing signature or timestamp header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Slack signature or timestamp header",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    # 1. Enforce 5-minute replay window
    try:
        ts_val = float(ts_header)
    except ValueError:
        logger.warning(f"Invalid X-Slack-Request-Timestamp value: {ts_header}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack timestamp format",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    now = time.time()
    if abs(now - ts_val) > max_replay_age:
        logger.warning(
            f"Slack webhook replay window violation: req_time={ts_val}, now={now}, diff={abs(now - ts_val):.1f}s"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Slack request timestamp expired (replay attack detected)",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    # 2. Build Slack signature basestring: v0:{timestamp}:{body}
    body_text = raw_body.decode("utf-8", errors="replace")
    base_string = f"v0:{ts_header}:{body_text}"

    computed_mac = hmac.new(
        secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    expected_sig = f"v0={computed_mac}"

    if not hmac.compare_digest(sig_header, expected_sig):
        logger.warning("Slack webhook signature mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack webhook signature",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )


def verify_signature_for_provider(
    provider: str,
    secret: str,
    raw_body: bytes,
    headers: Mapping[str, str],
) -> None:
    """Dispatches signature verification to the appropriate provider verifier."""
    prov = (provider or "").strip().lower()
    if prov == "github":
        verify_github_signature(secret, raw_body, headers)
    elif prov == "jira":
        verify_jira_signature(secret, raw_body, headers)
    elif prov == "slack":
        verify_slack_signature(secret, raw_body, headers)
    elif prov == "generic":
        signature_header = _get_header(headers, "X-Hub-Signature-256") or _get_header(
            headers, "X-Webhook-Signature"
        )
        if signature_header:
            computed_mac = hmac.new(
                secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
            if not (
                hmac.compare_digest(signature_header, f"sha256={computed_mac}")
                or hmac.compare_digest(signature_header, computed_mac)
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid generic webhook signature",
                    headers={"WWW-Authenticate": "WebhookSignature"},
                )
