"""Webhook Staging Latency Load Test Runner (SCRUM-251 / US-15).

Sends 200 HMAC-SHA256 signed webhook requests with realistic payload sizes
(GitHub push/PR, Jira issue_updated, Slack event_callback) to the integration
webhook intake endpoint (`/api/v1/webhooks/integrations/{integration_id}`).

Verifies:
1. All 200 signed requests return HTTP 202 Accepted (0% error rate, 0 unintended duplicates).
2. All 200 events are durably staged in PostgreSQL `webhook_events` (`status = 'staged'`)
   before acknowledgment.
3. Computes p50, p95, p99 (plus min, max, mean, p90) acknowledgment latency in milliseconds.
4. Fails with non-zero exit code if p95 >= 100.0 ms or if any request fails to produce 202.
5. Records structured benchmark results to `backend/reports/webhook_load_test_results.json`.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any
import uuid

import httpx

# Ensure backend root is on sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.integrations.crypto import encryption_service  # noqa: E402

logger = logging.getLogger("run_webhook_load_test")

LOADTEST_TENANT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
LOADTEST_TENANT_INFO = {
    "id": LOADTEST_TENANT_ID,
    "name": "Webhook Staging LoadTest Org",
    "slug": "webhook-staging-loadtest",
    "admin_email": "webhooks-admin@manta-loadtest.io",
    "admin_name": "Webhook LoadTest Admin",
}

PROVIDER_SECRETS = {
    "github": "whsec_github_loadtest_9f8e7d6c5b4a39281716151413121110fedcba987654",
    "jira": "whsec_jira_loadtest_1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f",
    "slack": "whsec_slack_loadtest_a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f6",
}

DEFAULT_MAX_P95_MS = 100.0


def normalize_dsn(dsn: str) -> str:
    """Ensure DSN uses asyncpg driver scheme and least-privilege eng_app_user role."""
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "manta_user:manta_password" in dsn:
        dsn = dsn.replace("manta_user:manta_password", "eng_app_user:eng_app_pass_change_in_prod")
    return dsn


def get_default_dsn() -> str:
    """Resolve database URL from environment (preferring PgBouncer test DSN when set)."""
    env_dsn = os.getenv("PGBOUNCER_TEST_DSN") or os.getenv("DATABASE_URL")
    if env_dsn:
        return normalize_dsn(env_dsn)
    return "postgresql+asyncpg://eng_app_user:eng_app_pass_change_in_prod@localhost:5432/manta_db"


def ensure_schema_migrated(dsn: str) -> None:
    """Ensure Alembic migrations have been applied up to head."""
    try:
        from alembic import command
        from alembic.config import Config

        mig_url = dsn
        if "eng_app_user" in mig_url:
            mig_url = mig_url.replace(
                "eng_app_user:eng_app_pass_change_in_prod", "manta_user:manta_password"
            )
        alembic_ini = BACKEND_DIR / "alembic.ini"
        if alembic_ini.exists():
            cfg = Config(str(alembic_ini))
            cfg.set_main_option("sqlalchemy.url", mig_url)
            cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
            command.upgrade(cfg, "head")
    except Exception as exc:
        logger.debug("Schema migration notice: %s", exc)


async def ensure_webhook_test_fixtures(dsn: str) -> dict[str, uuid.UUID]:
    """Provision the dedicated load-test tenant and encrypted GitHub/Jira/Slack integrations."""
    engine = create_async_engine(
        normalize_dsn(dsn),
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
        pool_pre_ping=True,
    )
    integration_ids: dict[str, uuid.UUID] = {}
    tid = LOADTEST_TENANT_INFO["id"]

    try:
        for attempt in range(2):
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                        {"tid": str(tid)},
                    )
                    row = await conn.execute(
                        text("SELECT id FROM tenants WHERE id = :tid"),
                        {"tid": tid},
                    )
                    if not row.first():
                        await conn.execute(
                            text(
                                """
                                SELECT * FROM provision_tenant(
                                    p_name => :name,
                                    p_slug => :slug,
                                    p_admin_email => :admin_email,
                                    p_admin_name => :admin_name,
                                    p_tier => 'enterprise',
                                    p_ai_processing_mode => 'cloud_zdr',
                                    p_tenant_id => :tid
                                )
                                """
                            ),
                            {
                                "name": LOADTEST_TENANT_INFO["name"],
                                "slug": LOADTEST_TENANT_INFO["slug"],
                                "admin_email": LOADTEST_TENANT_INFO["admin_email"],
                                "admin_name": LOADTEST_TENANT_INFO["admin_name"],
                                "tid": tid,
                            },
                        )

                    for provider, raw_secret in PROVIDER_SECRETS.items():
                        encrypted_secret = encryption_service.encrypt(raw_secret)
                        ext_acc = f"loadtest-{provider}-account"
                        res = await conn.execute(
                            text(
                                """
                                INSERT INTO integrations (
                                    id, tenant_id, provider, external_account_id,
                                    webhook_secret_hash, is_active, created_at, updated_at
                                )
                                VALUES (
                                    :id, :tenant_id, :provider, :ext_acc,
                                    :wh_secret, true, NOW(), NOW()
                                )
                                ON CONFLICT (tenant_id, provider, external_account_id)
                                DO UPDATE SET
                                    webhook_secret_hash = EXCLUDED.webhook_secret_hash,
                                    is_active = true,
                                    updated_at = NOW()
                                RETURNING id
                                """
                            ),
                            {
                                "id": uuid.uuid4(),
                                "tenant_id": tid,
                                "provider": provider,
                                "ext_acc": ext_acc,
                                "wh_secret": encrypted_secret,
                            },
                        )
                        integration_ids[provider] = res.scalar_one()
                break
            except Exception as exc:
                err_str = str(exc).lower()
                if attempt == 0 and ("does not exist" in err_str or "undefined" in err_str):
                    ensure_schema_migrated(dsn)
                    continue
                raise
    finally:
        await engine.dispose()

    return integration_ids


async def count_staged_events(dsn: str, tenant_id: uuid.UUID, event_ids: list[uuid.UUID]) -> int:
    """Verify under tenant RLS context that all returned event_ids exist with status='staged'."""
    if not event_ids:
        return 0

    engine = create_async_engine(
        normalize_dsn(dsn),
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            res = await conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM webhook_events
                    WHERE tenant_id = :tid
                      AND status = 'staged'
                      AND id = ANY(:ids)
                    """
                ),
                {"tid": tenant_id, "ids": event_ids},
            )
            return int(res.scalar_one() or 0)
    finally:
        await engine.dispose()


def build_realistic_github_payload(run_id: str, seq: int) -> tuple[bytes, str, str]:
    """Generate a realistic ~12-16 KB GitHub push / pull_request webhook payload."""
    is_pr = seq % 3 == 0
    event_name = "pull_request" if is_pr else "push"
    delivery_id = str(uuid.uuid4())
    commits = [
        {
            "id": hashlib.sha1(f"{run_id}-{seq}-c{i}".encode("utf-8")).hexdigest(),
            "tree_id": hashlib.sha1(f"tree-{run_id}-{seq}-{i}".encode("utf-8")).hexdigest(),
            "distinct": True,
            "message": (
                f"feat(ingestion): optimize durable event staging pipeline batch #{seq}-{i}\n\n"
                "Ensures strict tenant RLS context propagation through PgBouncer transaction "
                "pooling with constant-time HMAC-SHA256 verification and idempotent conflict handling."
            ),
            "timestamp": "2026-11-01T12:00:00Z",
            "url": f"https://github.com/manta-org/platform-core/commit/{seq:04d}{i:02d}",
            "author": {
                "name": f"Engineer {seq % 12}",
                "email": f"eng{seq % 12}@manta-loadtest.io",
                "username": f"manta-dev-{seq % 12}",
            },
            "committer": {
                "name": "GitHub Enterprise",
                "email": "noreply@github.com",
                "username": "web-flow",
            },
            "added": [f"backend/app/webhooks/handlers/module_{seq}_{i}.py"],
            "removed": [],
            "modified": [
                "backend/app/webhooks/router.py",
                "backend/app/webhooks/service.py",
                f"backend/tests/test_module_{i}.py",
            ],
        }
        for i in range(12)
    ]

    payload: dict[str, Any] = {
        "ref": f"refs/heads/feature/SCRUM-251-batch-{seq}",
        "before": hashlib.sha1(f"before-{run_id}-{seq}".encode("utf-8")).hexdigest(),
        "after": hashlib.sha1(f"after-{run_id}-{seq}".encode("utf-8")).hexdigest(),
        "created": False,
        "deleted": False,
        "forced": False,
        "compare": f"https://github.com/manta-org/platform-core/compare/main...batch-{seq}",
        "commits": commits,
        "head_commit": commits[-1],
        "repository": {
            "id": 90421000 + (seq % 25),
            "node_id": f"R_kgDO{run_id[:8]}{seq:03d}",
            "name": "platform-core",
            "full_name": "manta-org/platform-core",
            "private": True,
            "default_branch": "develop",
            "language": "Python",
            "stargazers_count": 42,
            "forks_count": 8,
            "open_issues_count": 19,
            "html_url": "https://github.com/manta-org/platform-core",
            "clone_url": "https://github.com/manta-org/platform-core.git",
            "owner": {
                "login": "manta-org",
                "id": 44001122,
                "type": "Organization",
                "site_admin": False,
            },
        },
        "organization": {
            "login": "manta-org",
            "id": 44001122,
            "description": "AI-Augmented Engineering Intelligence Platform",
        },
        "pusher": {
            "name": f"manta-dev-{seq % 12}",
            "email": f"eng{seq % 12}@manta-loadtest.io",
        },
        "sender": {
            "login": f"manta-dev-{seq % 12}",
            "id": 7000 + (seq % 12),
            "type": "User",
            "avatar_url": f"https://avatars.githubusercontent.com/u/{7000 + (seq % 12)}?v=4",
        },
    }

    if is_pr:
        payload["action"] = "synchronize"
        payload["number"] = 400 + seq
        payload["pull_request"] = {
            "id": 2100000 + seq,
            "number": 400 + seq,
            "state": "open",
            "title": f"[SCRUM-251] Durable webhook staging benchmark #{seq}",
            "body": (
                "## Summary\n"
                "- Validates durable PostgreSQL `webhook_events` staging before HTTP 202.\n"
                "- Verifies HMAC-SHA256 signature validation across realistic payload sizes.\n"
                "- Confirms p95 latency stays strictly below 100 ms under burst load.\n\n"
                + ("Detailed diff summary line for realistic PR description sizing.\n" * 20)
            ),
            "draft": False,
            "commits": 12,
            "additions": 284,
            "deletions": 47,
            "changed_files": 15,
            "head": {"ref": f"feature/SCRUM-251-{seq}", "sha": commits[-1]["id"]},
            "base": {"ref": "develop", "sha": payload["before"]},
        }

    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw_body, delivery_id, event_name


def build_realistic_jira_payload(run_id: str, seq: int) -> tuple[bytes, str, str]:
    """Generate a realistic ~8-11 KB Jira Cloud issue_updated webhook payload."""
    delivery_id = f"jira-{run_id}-{seq:04d}-{uuid.uuid4().hex[:8]}"
    event_name = "jira:issue_updated"
    acceptance_criteria_block = "\n".join(
        f"- [x] Criterion {i}: Ensure tenant-isolated event staging completes durably "
        f"with zero cross-tenant state leakage and deterministic retry semantics (run={run_id})."
        for i in range(1, 18)
    )
    changelog_items = [
        {
            "field": "status",
            "fieldtype": "jira",
            "fieldId": "status",
            "from": "10001",
            "fromString": "Selected for Development",
            "to": "10002",
            "toString": "In Progress",
        },
        {
            "field": "description",
            "fieldtype": "jira",
            "fieldId": "description",
            "from": None,
            "fromString": "Initial user story specification draft.",
            "to": None,
            "toString": acceptance_criteria_block,
        },
        {
            "field": "Story Points",
            "fieldtype": "custom",
            "fieldId": "customfield_10016",
            "from": None,
            "fromString": "1",
            "to": None,
            "toString": "2",
        },
    ]

    payload = {
        "timestamp": int(time.time() * 1000),
        "webhookEvent": event_name,
        "issue_event_type_name": "issue_updated",
        "user": {
            "self": "https://manta.atlassian.net/rest/api/2/user?accountId=557058:loadtest",
            "accountId": f"557058:user-{seq % 10:03d}",
            "displayName": f"Product Owner {seq % 10}",
            "active": True,
            "timeZone": "Europe/Kyiv",
            "accountType": "atlassian",
        },
        "issue": {
            "id": str(10500 + seq),
            "self": f"https://manta.atlassian.net/rest/api/2/105{seq:02d}",
            "key": f"SCRUM-{300 + seq}",
            "fields": {
                "summary": f"Durable webhook intake & staging verification scenario #{seq}",
                "description": (
                    "### Objective\n"
                    "Measure whether durable PostgreSQL staging meets the <100 ms p95 latency goal "
                    "under signed multi-provider webhook bursts.\n\n"
                    "### Acceptance Criteria\n"
                    f"{acceptance_criteria_block}"
                ),
                "issuetype": {"id": "10001", "name": "Story", "subtask": False},
                "project": {
                    "id": "10000",
                    "key": "SCRUM",
                    "name": "MANTA Engineering Intelligence",
                    "projectTypeKey": "software",
                },
                "priority": {"id": "2", "name": "High"},
                "labels": ["webhooks", "durability", "us-15", "load-test", f"batch-{seq % 5}"],
                "status": {
                    "id": "10002",
                    "name": "In Progress",
                    "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress"},
                },
                "customfield_10020": [
                    {
                        "id": 42,
                        "name": "MANTA Sprint 4",
                        "state": "active",
                        "goal": "Complete US-15 durable webhook ingestion & OIDC authentication",
                    }
                ],
            },
        },
        "changelog": {"id": str(88000 + seq), "items": changelog_items},
    }

    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw_body, delivery_id, event_name


def build_realistic_slack_payload(run_id: str, seq: int) -> tuple[bytes, str, str]:
    """Generate a realistic ~4-6 KB Slack Events API event_callback webhook payload."""
    event_id = f"Ev{run_id[:8].upper()}{seq:04d}"
    now_epoch = int(time.time())
    blocks = [
        {
            "type": "section",
            "block_id": f"blk_{seq}_{i}",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*[Blocker Thread Update #{seq}-{i}]* Waiting on upstream schema review for "
                    f"`SCRUM-{250 + (seq % 20)}` before merging PR #{400 + seq}. "
                    "Latency budget check: webhook staging must remain durable prior to 202 ACK."
                ),
            },
        }
        for i in range(10)
    ]

    payload = {
        "token": "deprecated_verification_token_placeholder",
        "team_id": "T07MANTADEV",
        "context_team_id": "T07MANTADEV",
        "api_app_id": "A07MANTABOT",
        "event": {
            "type": "message",
            "subtype": None,
            "user": f"U07DEV{seq % 15:03d}",
            "ts": f"{now_epoch}.{seq:06d}",
            "thread_ts": f"{now_epoch - 120}.000100",
            "channel": f"C07ENG{seq % 4:02d}",
            "channel_type": "channel",
            "event_ts": f"{now_epoch}.{seq:06d}",
            "text": (
                f"Engineering blocker thread check #{seq}: verifying durable webhook staging "
                f"and p95 latency SLA (<100ms) for run {run_id}."
            ),
            "blocks": blocks,
        },
        "type": "event_callback",
        "event_id": event_id,
        "event_time": now_epoch,
        "authorizations": [
            {
                "enterprise_id": None,
                "team_id": "T07MANTADEV",
                "user_id": "U07BOTUSER",
                "is_bot": True,
                "is_enterprise_install": False,
            }
        ],
    }

    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw_body, event_id, "event_callback"


def sign_webhook_headers(
    provider: str,
    secret: str,
    raw_body: bytes,
    delivery_id: str,
    event_name: str,
) -> dict[str, str]:
    """Compute authentic provider HMAC-SHA256 signature headers over raw_body."""
    headers = {"Content-Type": "application/json"}

    if provider == "github":
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        headers["X-GitHub-Delivery"] = delivery_id
        headers["X-GitHub-Event"] = event_name
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    elif provider == "jira":
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        headers["X-Atlassian-Webhook-Identifier"] = delivery_id
        headers["X-Atlassian-Webhook-Event"] = event_name
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    elif provider == "slack":
        ts_str = str(int(time.time()))
        basestring = f"v0:{ts_str}:{raw_body.decode('utf-8')}"
        digest = hmac.new(
            secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        headers["X-Slack-Request-Timestamp"] = ts_str
        headers["X-Slack-Signature"] = f"v0={digest}"
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    return headers


def _percentile_nearest_rank_or_interp(sorted_vals: list[float], pct: float) -> float:
    """Compute percentile (0-100) accurately for arbitrary non-empty sample sizes."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    # Inclusive linear interpolation (consistent with numpy/perf tooling) and check vs nearest-rank
    pos = (pct / 100.0) * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    weight = pos - lo
    return sorted_vals[lo] * (1.0 - weight) + sorted_vals[hi] * weight


def compute_latency_percentiles(latencies_ms: list[float]) -> dict[str, float]:
    """Compute min, max, mean, p50, p90, p95, p99 latency statistics in ms."""
    if not latencies_ms:
        return {
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
        }
    ordered = sorted(latencies_ms)
    return {
        "min": round(ordered[0], 2),
        "max": round(ordered[-1], 2),
        "mean": round(statistics.mean(ordered), 2),
        "p50": round(_percentile_nearest_rank_or_interp(ordered, 50.0), 2),
        "p90": round(_percentile_nearest_rank_or_interp(ordered, 90.0), 2),
        "p95": round(_percentile_nearest_rank_or_interp(ordered, 95.0), 2),
        "p99": round(_percentile_nearest_rank_or_interp(ordered, 99.0), 2),
    }


def evaluate_load_test_gate(
    stats: dict[str, Any],
    max_p95_ms: float = DEFAULT_MAX_P95_MS,
) -> tuple[bool, list[str]]:
    """Evaluate US-15 / SCRUM-251 acceptance criteria.

    Fails if:
    - p95 latency is 100 ms or more (`p95 >= max_p95_ms`),
    - error rate > 0% or any request did not return HTTP 202,
    - staged database row count does not match total requests.
    """
    reasons: list[str] = []
    total = int(stats.get("total_requests", 0))
    status_202 = int(stats.get("status_202_count", 0))
    failed = int(stats.get("failed_requests", 0))
    err_rate = float(stats.get("error_rate_percent", 0.0))
    staged_rows = int(stats.get("verified_staged_rows", 0))
    lat = stats.get("latency_ms", {})
    p95_val = float(lat.get("p95", 0.0))
    raw_p95 = float(stats.get("_raw_p95_ms", p95_val))

    if total <= 0:
        reasons.append("Total requests executed must be greater than 0")
    if status_202 != total:
        reasons.append(f"Expected all {total} requests to produce HTTP 202, got {status_202}")
    if failed > 0 or err_rate > 0.0:
        reasons.append(f"Non-zero error rate: {failed} failed requests ({err_rate}%)")
    if staged_rows != total:
        reasons.append(
            f"Expected {total} durably staged rows in webhook_events, verified {staged_rows}"
        )
    if raw_p95 >= max_p95_ms or p95_val >= max_p95_ms:
        reasons.append(
            f"p95 acknowledgment latency {p95_val:.2f} ms meets or exceeds failure threshold "
            f"({max_p95_ms:.2f} ms)"
        )

    return len(reasons) == 0, reasons


class WebhookLoadTestRunner:
    """Executes signed realistic webhook load tests and verifies <100 ms p95 staging SLA."""

    def __init__(
        self,
        dsn: str | None = None,
        base_url: str = "http://localhost:8000",
        total_requests: int = 200,
        concurrency: int = 15,
        in_process: bool = False,
        warmup_requests: int = 3,
        max_p95_ms: float = DEFAULT_MAX_P95_MS,
        report_path: Path | None = None,
    ) -> None:
        self.dsn = normalize_dsn(dsn or get_default_dsn())
        self.base_url = base_url.rstrip("/")
        self.total_requests = total_requests
        self.concurrency = concurrency
        self.in_process = in_process
        self.warmup_requests = warmup_requests
        self.max_p95_ms = max_p95_ms
        self.report_path = report_path or (
            BACKEND_DIR / "reports" / "webhook_load_test_results.json"
        )

    def _build_prepared_requests(
        self,
        run_id: str,
        integration_ids: dict[str, uuid.UUID],
        count: int,
    ) -> list[dict[str, Any]]:
        """Pre-build realistic signed webhook payloads so client generation overhead isn't timed."""
        prepared: list[dict[str, Any]] = []
        # Realistic distribution across GitHub (40%), Jira (30%), Slack (30%)
        provider_cycle = [
            "github",
            "github",
            "jira",
            "slack",
            "github",
            "jira",
            "slack",
            "github",
            "jira",
            "slack",
        ]

        for idx in range(count):
            provider = provider_cycle[idx % len(provider_cycle)]
            if provider == "github":
                raw_body, delivery_id, event_name = build_realistic_github_payload(run_id, idx)
            elif provider == "jira":
                raw_body, delivery_id, event_name = build_realistic_jira_payload(run_id, idx)
            else:
                raw_body, delivery_id, event_name = build_realistic_slack_payload(run_id, idx)

            prepared.append(
                {
                    "idx": idx,
                    "provider": provider,
                    "integration_id": integration_ids[provider],
                    "path": f"/api/v1/webhooks/integrations/{integration_ids[provider]}",
                    "raw_body": raw_body,
                    "payload_bytes": len(raw_body),
                    "delivery_id": delivery_id,
                    "event_name": event_name,
                }
            )
        return prepared

    async def _send_one(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        """Send one signed webhook request and measure exact acknowledgment latency."""
        provider = item["provider"]
        raw_body = item["raw_body"]

        async with semaphore:
            # Generate signature immediately prior to dispatch (keeps Slack timestamp fresh)
            headers = sign_webhook_headers(
                provider=provider,
                secret=PROVIDER_SECRETS[provider],
                raw_body=raw_body,
                delivery_id=item["delivery_id"],
                event_name=item["event_name"],
            )

            start = time.perf_counter()
            try:
                response = await client.post(
                    item["path"],
                    content=raw_body,
                    headers=headers,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                body_json = response.json() if response.status_code == 202 else {}
                event_id_raw = body_json.get("event_id")
                is_dup = bool(body_json.get("duplicate", False))

                return {
                    "idx": item["idx"],
                    "provider": provider,
                    "payload_bytes": item["payload_bytes"],
                    "status_code": response.status_code,
                    "latency_ms": elapsed_ms,
                    "success": response.status_code == 202 and not is_dup and bool(event_id_raw),
                    "duplicate": is_dup,
                    "event_id": uuid.UUID(event_id_raw) if event_id_raw else None,
                    "error": None
                    if response.status_code == 202
                    else f"HTTP {response.status_code}: {response.text[:200]}",
                }
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return {
                    "idx": item["idx"],
                    "provider": provider,
                    "payload_bytes": item["payload_bytes"],
                    "status_code": 0,
                    "latency_ms": elapsed_ms,
                    "success": False,
                    "duplicate": False,
                    "event_id": None,
                    "error": str(exc),
                }

    async def run(self) -> dict[str, Any]:
        """Execute provisioning, warm-up, 200 signed requests, DB durability check, and reporting."""
        integration_ids = await ensure_webhook_test_fixtures(self.dsn)
        run_id = uuid.uuid4().hex[:12]

        limits = httpx.Limits(
            max_connections=max(self.concurrency * 2, 40),
            max_keepalive_connections=max(self.concurrency, 20),
        )
        timeout = httpx.Timeout(30.0)

        transport_mode = "in_process_asgi" if self.in_process else "live_http"
        if self.in_process:
            from app.main import app

            client_ctx = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                limits=limits,
                timeout=timeout,
            )
        else:
            client_ctx = httpx.AsyncClient(
                base_url=self.base_url,
                limits=limits,
                timeout=timeout,
            )

        try:
            async with client_ctx as client:
                # 1. Optional connection pool warm-up (excluded from measured 200 requests)
                if self.warmup_requests > 0:
                    warmup_items = self._build_prepared_requests(
                        f"warm{run_id[:8]}", integration_ids, self.warmup_requests
                    )
                    warm_sem = asyncio.Semaphore(self.warmup_requests)
                    await asyncio.gather(
                        *(self._send_one(client, w, warm_sem) for w in warmup_items)
                    )

                # 2. Build 200 realistic signed webhook payloads
                prepared = self._build_prepared_requests(
                    run_id, integration_ids, self.total_requests
                )
                semaphore = asyncio.Semaphore(self.concurrency)

                overall_start = time.perf_counter()
                results = await asyncio.gather(
                    *(self._send_one(client, item, semaphore) for item in prepared)
                )
                total_duration_s = time.perf_counter() - overall_start
        finally:
            if self.in_process:
                from app.db.session import async_engine

                await async_engine.dispose()

        # 3. Verify durable persistence in PostgreSQL webhook_events under RLS
        event_ids = [r["event_id"] for r in results if r["event_id"] is not None]
        verified_staged_rows = await count_staged_events(self.dsn, LOADTEST_TENANT_ID, event_ids)

        # 4. Aggregate metrics
        latencies = [r["latency_ms"] for r in results]
        ordered_lat = sorted(latencies)
        raw_p95 = _percentile_nearest_rank_or_interp(ordered_lat, 95.0) if ordered_lat else 0.0
        latency_summary = compute_latency_percentiles(latencies)

        payload_sizes = [r["payload_bytes"] for r in results]
        status_counts: dict[str, int] = {}
        provider_counts: dict[str, int] = {}
        for r in results:
            code_key = str(r["status_code"])
            status_counts[code_key] = status_counts.get(code_key, 0) + 1
            prov = r["provider"]
            provider_counts[prov] = provider_counts.get(prov, 0) + 1

        status_202_count = sum(1 for r in results if r["status_code"] == 202)
        duplicate_count = sum(1 for r in results if r["duplicate"])
        failures = [r for r in results if not r["success"]]
        error_rate_pct = (
            (len(failures) / self.total_requests) * 100.0 if self.total_requests else 0.0
        )

        stats: dict[str, Any] = {
            "task": "SCRUM-251 / US-15: Durable event staging before acknowledgment",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "transport_mode": transport_mode,
            "target_base_url": "asgi://app" if self.in_process else self.base_url,
            "target_dsn": self.dsn.split("@")[-1],
            "total_requests": self.total_requests,
            "concurrency": self.concurrency,
            "warmup_requests": self.warmup_requests,
            "total_duration_seconds": round(total_duration_s, 3),
            "throughput_req_per_sec": (
                round(self.total_requests / total_duration_s, 2) if total_duration_s > 0 else 0.0
            ),
            "status_202_count": status_202_count,
            "status_code_distribution": status_counts,
            "provider_distribution": provider_counts,
            "duplicate_count": duplicate_count,
            "successful_requests": self.total_requests - len(failures),
            "failed_requests": len(failures),
            "error_rate_percent": round(error_rate_pct, 2),
            "first_failure_error": failures[0]["error"] if failures else None,
            "verified_staged_rows": verified_staged_rows,
            "payload_size_bytes": {
                "min": min(payload_sizes) if payload_sizes else 0,
                "mean": round(statistics.mean(payload_sizes), 1) if payload_sizes else 0.0,
                "max": max(payload_sizes) if payload_sizes else 0,
                "total": sum(payload_sizes),
            },
            "latency_ms": latency_summary,
            "sla_threshold_p95_ms": self.max_p95_ms,
            "_raw_p95_ms": raw_p95,
        }

        passed, failure_reasons = evaluate_load_test_gate(stats, max_p95_ms=self.max_p95_ms)
        stats.pop("_raw_p95_ms", None)
        stats["passed"] = passed
        stats["failure_reasons"] = failure_reasons

        # 5. Record results to JSON report
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
            f.write("\n")

        return stats


def print_summary(stats: dict[str, Any], report_path: Path) -> None:
    """Print human-readable load test summary to stdout."""
    lat = stats["latency_ms"]
    psize = stats["payload_size_bytes"]
    verdict = "PASSED" if stats["passed"] else "FAILED"
    print("\n" + "=" * 70)
    print("   WEBHOOK STAGING LATENCY LOAD TEST (SCRUM-251 / US-15)")
    print("=" * 70)
    print(f" Target Endpoint:          {stats['target_base_url']} ({stats['transport_mode']})")
    print(f" Database Target:          {stats['target_dsn']}")
    print(f" Total Signed Requests:    {stats['total_requests']}")
    print(f" Provider Mix:             {stats['provider_distribution']}")
    print(
        f" Realistic Payload Size:   min={psize['min']:,} B | "
        f"mean={psize['mean']:,.0f} B | max={psize['max']:,} B"
    )
    print(f" Concurrency Limit:        {stats['concurrency']}")
    print(f" Total Duration:           {stats['total_duration_seconds']} s")
    print(f" Throughput:               {stats['throughput_req_per_sec']} req/s")
    print("-" * 70)
    print(f" HTTP 202 Responses:       {stats['status_202_count']} / {stats['total_requests']}")
    print(f" Durably Staged in DB:     {stats['verified_staged_rows']} / {stats['total_requests']}")
    print(f" Failed Requests:          {stats['failed_requests']}")
    print(f" Error Rate:               {stats['error_rate_percent']}%")
    print("-" * 70)
    print(" Acknowledgment Latency (ms):")
    print(f"   Min:    {lat['min']:>7.2f} ms")
    print(f"   Mean:   {lat['mean']:>7.2f} ms")
    print(f"   p50:    {lat['p50']:>7.2f} ms")
    print(f"   p90:    {lat['p90']:>7.2f} ms")
    print(
        f"   p95:    {lat['p95']:>7.2f} ms   (SLA Gate: < {stats['sla_threshold_p95_ms']:.1f} ms)"
    )
    print(f"   p99:    {lat['p99']:>7.2f} ms")
    print(f"   Max:    {lat['max']:>7.2f} ms")
    print("-" * 70)
    print(f" Recorded Report:          {report_path}")
    print(f" Gate Verdict:             [{verdict}]")
    if stats["failure_reasons"]:
        for reason in stats["failure_reasons"]:
            print(f"   - {reason}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="US-15 / SCRUM-251 Webhook Staging Latency Load Test (200 signed requests)"
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("WEBHOOK_LOADTEST_BASE_URL", "http://localhost:8000"),
        help="Target FastAPI base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dsn",
        type=str,
        default=None,
        help="Database DSN for fixture setup and staged-row verification",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=200,
        help="Number of signed webhook requests to send (default: 200)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="Concurrent in-flight webhook requests (default: 15)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warm-up requests before timed measurement (default: 3)",
    )
    parser.add_argument(
        "--max-p95-ms",
        type=float,
        default=DEFAULT_MAX_P95_MS,
        help="Strict upper bound for p95 latency in ms; fails if p95 >= value (default: 100.0)",
    )
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Use in-process ASGI transport instead of live TCP HTTP server",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Custom output path for JSON benchmark report",
    )
    args = parser.parse_args()

    report_path = (
        Path(args.report)
        if args.report
        else (BACKEND_DIR / "reports" / "webhook_load_test_results.json")
    )

    runner = WebhookLoadTestRunner(
        dsn=args.dsn,
        base_url=args.base_url,
        total_requests=args.requests,
        concurrency=args.concurrency,
        in_process=args.in_process,
        warmup_requests=args.warmup,
        max_p95_ms=args.max_p95_ms,
        report_path=report_path,
    )

    stats = asyncio.run(runner.run())
    print_summary(stats, runner.report_path)

    if not stats["passed"]:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
