"""
SCRUM-424 (Task 24.3): Provider Version Extractors & `dropped_stale_event_total` Prometheus Metric.

Verifies:
1. `extract_entity_version(provider, payload)` deterministic extraction and edge cases
   for `github`, `jira`, and `slack` (plus `ValueError` on unsupported providers).
2. Prometheus `dropped_stale_event_total{provider=...}` registration, pre-initialized series,
   strict label cardinality, and `GET /metrics` public exposition.
3. Exact-once (+1) counter increments across all 0-row stale update paths:
   - `execute_versioned_update` (`pull_requests`, `issues`, `communication_events`)
   - `process_pull_request` (`_is_stale` terminal guard & conditional SQL guard)
   - `upsert_issue` (early `changelog_id` precedence, cross-domain `changelog_id IS NULL` guard,
     and conditional SQL guard)
   - `upsert_communication_event_versioned` (`ON CONFLICT ... WHERE entity_version < EXCLUDED.entity_version`)
4. Worker loop (`run_worker_fetch_loop(..., retry_on_failure=True)`) completes stale jobs cleanly
   without raising exceptions, retrying, or writing to `dead_letter_events`.
5. Live PostgreSQL E2E verification through `process_staged_deployment_event_in_session`
   under tenant RLS for GitHub, Jira, and Slack.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
import pytest
from sqlalchemy import func, select, text

from app.db.models.models import (
    CommunicationEvent,
    DeadLetterEvent,
    Integration,
    Issue,
    Project,
    PullRequest,
    Repository,
    WebhookEvent,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.git.normalization import normalize_pull_request
from app.git.service import process_pull_request
from app.jira.normalization import normalize_jira_event
from app.jira.service import upsert_issue
from app.main import app
from app.webhooks.metrics import (
    DROPPED_STALE_EVENT_TOTAL,
    SUPPORTED_STALE_EVENT_PROVIDERS,
    dropped_stale_event_total,
    get_dropped_stale_event_count,
    record_dropped_stale_event,
)
from app.webhooks.processor import process_staged_deployment_event_in_session
from app.webhooks.slack_processor import upsert_communication_event_versioned
from app.webhooks.versioning import (
    execute_versioned_update,
    extract_entity_version,
    parse_slack_ts_to_micros,
    parse_timestamp_to_epoch_ms,
)
from workers.worker import (
    QUEUE_DEFAULT,
    enqueue_job,
    get_in_flight_key,
    run_worker_fetch_loop,
    serialize_job_payload,
)


def _metric_sample(provider: str) -> float:
    val = REGISTRY.get_sample_value("dropped_stale_event_total", {"provider": provider})
    assert val is not None, f"Metric dropped_stale_event_total{{provider={provider!r}}} missing"
    return float(val)


async def _is_live_pg_ready() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception:
        return False


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    uid = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            text(
                """
                SELECT tenant_id
                FROM provision_tenant(
                    p_name => :name,
                    p_slug => :slug,
                    p_admin_email => :admin_email,
                    p_admin_name => :admin_name
                )
                """
            ),
            {
                "name": f"Org {slug_prefix} {uid}",
                "slug": f"{slug_prefix}-{uid}",
                "admin_email": f"{slug_prefix}-{uid}@test.local",
                "admin_name": f"Admin {uid}",
            },
        )
        tid = res.scalar_one()
        await db.commit()
        return tid


# ==============================================================================
# 1. Unit Tests: extract_entity_version(provider, payload)
# ==============================================================================


class TestExtractEntityVersion:
    """Comprehensive unit tests for GitHub, Jira, and Slack version extractors."""

    def test_github_extracts_utc_epoch_ms_and_fallback_chain(self) -> None:
        # Primary: pull_request.updated_at with fractional ms & Z
        payload = {
            "action": "synchronize",
            "pull_request": {
                "number": 101,
                "created_at": "2026-11-19T10:00:00Z",
                "updated_at": "2026-11-19T12:34:56.789Z",
            },
        }
        expected_ms = int(
            (
                datetime(2026, 11, 19, 12, 34, 56, 789000, tzinfo=timezone.utc)
                - datetime(1970, 1, 1, tzinfo=timezone.utc)
            ).total_seconds()
            * 1000
        )
        assert extract_entity_version("github", payload) == expected_ms
        assert extract_entity_version("  GitHub ", payload) == expected_ms

        # Non-colon offset +0200 (14:34:56.789+0200 == 12:34:56.789Z)
        payload_offset = {
            "pull_request": {"updated_at": "2026-11-19T14:34:56.789+0200"},
        }
        assert extract_entity_version("github", payload_offset) == expected_ms

        # Fallback: payload.updated_at -> merged_at -> closed_at -> created_at -> entity_version
        assert extract_entity_version("github", {"updated_at": "2026-11-19T12:34:56.789Z"}) == (
            expected_ms
        )
        assert (
            extract_entity_version(
                "github",
                {"pull_request": {"merged_at": "2026-11-19T12:34:56.789Z"}},
            )
            == expected_ms
        )
        assert (
            extract_entity_version(
                "github",
                {"pull_request": {"closed_at": "2026-11-19T12:34:56.789Z"}},
            )
            == expected_ms
        )
        created_ver = extract_entity_version(
            "github",
            {"action": "opened", "pull_request": {"created_at": "2026-11-19T10:00:00Z"}},
        )
        assert created_ver is not None and created_ver < expected_ms
        assert extract_entity_version("github", {"pull_request": {"entity_version": 42}}) == 42

        # Missing or invalid values return None
        assert extract_entity_version("github", {}) is None
        assert extract_entity_version("github", {"pull_request": {"updated_at": True}}) is None
        assert extract_entity_version("github", {"pull_request": {"updated_at": -5}}) is None
        assert (
            extract_entity_version("github", {"pull_request": {"updated_at": "not-a-date"}}) is None
        )

    def test_jira_extracts_changelog_id_and_updated_fallback(self) -> None:
        # Primary: changelog.id strictly overrides issue.fields.updated
        payload_cl = {
            "webhookEvent": "jira:issue_updated",
            "changelog": {"id": "205", "items": []},
            "issue": {
                "key": "SCRUM-424",
                "fields": {"updated": "2026-11-19T11:00:00.000+0000"},
            },
        }
        assert extract_entity_version("jira", payload_cl) == 205
        assert extract_entity_version("JIRA", {"changelog": {"id": 204}}) == 204
        assert extract_entity_version("jira", {"changelog_id": "199"}) == 199

        # Invalid/non-positive changelog.id falls through to issue.fields.updated
        payload_bad_cl = {
            "changelog": {"id": "0"},
            "issue": {"fields": {"updated": "2026-11-19T11:00:00.250+0000"}},
        }
        expected_ms = parse_timestamp_to_epoch_ms("2026-11-19T11:00:00.250+0000")
        assert expected_ms is not None
        assert extract_entity_version("jira", payload_bad_cl) == expected_ms

        # Numeric string timestamp fallback (epoch ms)
        assert extract_entity_version("jira", {"timestamp": "1731326400123"}) == 1731326400123
        assert extract_entity_version("jira", {"timestamp": 1731326400}) == 1731326400000

        # Created fallback and explicit entity_version fallback
        assert extract_entity_version(
            "jira",
            {"issue": {"fields": {"created": "2026-11-19T09:00:00.000+0000"}}},
        ) == parse_timestamp_to_epoch_ms("2026-11-19T09:00:00.000+0000")
        assert extract_entity_version("jira", {"entity_version": 77}) == 77
        assert extract_entity_version("jira", {"changelog": {"id": True}}) is None

    def test_slack_extracts_exact_decimal_microseconds_and_message_changed(self) -> None:
        # Exact 6-digit microsecond precision (avoids float drift)
        assert parse_slack_ts_to_micros("1700000010.000200") == 1700000010000200
        assert parse_slack_ts_to_micros("1700000005.000100") == 1700000005000100
        assert parse_slack_ts_to_micros("1731326400.123456") == 1731326400123456

        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "channel": "C100",
                "ts": "1700000010.000100",
                "event_ts": "1700000010.000200",
            },
        }
        assert extract_entity_version("slack", payload) == 1700000010000200

        # Nested message_changed payload fallback (event.message.edited.ts -> event.message.ts)
        payload_edited = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "subtype": "message_changed",
                "channel": "C100",
                "message": {
                    "ts": "1700000005.000100",
                    "edited": {"ts": "1700000012.000345"},
                },
            },
        }
        assert extract_entity_version("slack", payload_edited) == 1700000012000345

        # Fallback to event_time (seconds -> microseconds) and explicit entity_version
        assert extract_entity_version("slack", {"event_time": 1700000020}) == 1700000020000000
        assert extract_entity_version("slack", {"entity_version": 999}) == 999

        # Non-finite / negative / boolean / malformed values never raise
        for bad in ("NaN", "Infinity", "-Infinity", "-1.5", "", True, None):
            assert parse_slack_ts_to_micros(bad) is None
        assert extract_entity_version("slack", {"event": {"event_ts": "NaN"}}) is None

    def test_unsupported_provider_raises_value_error(self) -> None:
        for bad_provider in ("gitlab", "argocd", "bitbucket", "", "   "):
            with pytest.raises(ValueError, match="Unsupported provider"):
                extract_entity_version(bad_provider, {})

    def test_non_mapping_payload_returns_none_safely(self) -> None:
        for provider in ("github", "jira", "slack"):
            assert extract_entity_version(provider, None) is None  # type: ignore[arg-type]
            assert extract_entity_version(provider, []) is None  # type: ignore[arg-type]

    def test_parse_timestamp_to_epoch_ms_rejects_non_finite_and_handles_decimal(self) -> None:
        assert parse_timestamp_to_epoch_ms(float("nan")) is None
        assert parse_timestamp_to_epoch_ms(float("inf")) is None
        assert parse_timestamp_to_epoch_ms(Decimal("1731326400.123")) == 1731326400123
        assert parse_timestamp_to_epoch_ms(0) is None
        assert parse_timestamp_to_epoch_ms(-100) is None


# ==============================================================================
# 2. Unit & HTTP Tests: Prometheus Counter & /metrics Endpoint
# ==============================================================================


class TestPrometheusStaleEventMetric:
    """Verifies registration, label enforcement, delta accuracy, and public /metrics route."""

    def test_counter_registered_and_all_providers_pre_initialized(self) -> None:
        assert DROPPED_STALE_EVENT_TOTAL is not None
        assert dropped_stale_event_total is DROPPED_STALE_EVENT_TOTAL
        assert SUPPORTED_STALE_EVENT_PROVIDERS == frozenset({"github", "jira", "slack"})

        for provider in ("github", "jira", "slack"):
            sample_val = _metric_sample(provider)
            assert sample_val == get_dropped_stale_event_count(provider)

    def test_record_dropped_stale_event_increments_exact_provider_only(self) -> None:
        before = {p: _metric_sample(p) for p in ("github", "jira", "slack")}

        new_jira = record_dropped_stale_event("  Jira ")
        assert new_jira == before["jira"] + 1.0
        assert _metric_sample("jira") == before["jira"] + 1.0
        assert _metric_sample("github") == before["github"]
        assert _metric_sample("slack") == before["slack"]

    def test_record_dropped_stale_event_validates_provider_and_amount(self) -> None:
        with pytest.raises(ValueError, match="Unsupported provider"):
            record_dropped_stale_event("argocd")
        with pytest.raises(ValueError, match="amount must be a positive integer"):
            record_dropped_stale_event("github", amount=0)
        with pytest.raises(ValueError, match="amount must be a positive integer"):
            record_dropped_stale_event("slack", amount=-1)
        with pytest.raises(ValueError, match="amount must be a positive integer"):
            record_dropped_stale_event("jira", amount=True)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_metrics_endpoint_is_public_and_exports_counter(self) -> None:
        before_gh = _metric_sample("github")
        record_dropped_stale_event("github")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/metrics")

        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "# TYPE dropped_stale_event_total counter" in body
        assert f'dropped_stale_event_total{{provider="github"}} {before_gh + 1.0}' in body
        assert 'dropped_stale_event_total{provider="jira"}' in body
        assert 'dropped_stale_event_total{provider="slack"}' in body


# ==============================================================================
# 3. Mock-Session Tests: Exact-Once Counter Increments & Edge-Case Guards
# ==============================================================================


class _MockResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _MockVersionedSession:
    """In-memory mock session simulating conditional PostgreSQL version guards."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}

    def seed(
        self,
        table: str,
        rid: uuid.UUID,
        tid: uuid.UUID,
        ver: int,
        *,
        title: str = "Initial",
        changelog_id: int | None = None,
    ) -> None:
        self.rows[(table, rid)] = {
            "tenant_id": tid,
            "entity_version": ver,
            "title": title,
            "changelog_id": changelog_id,
        }

    async def execute(self, stmt: Any) -> Any:
        compiled = stmt.compile()
        cparams = compiled.params
        table_name = stmt.table.name
        rid = cparams["id_1"]
        tid = cparams["tenant_id_1"]
        threshold = cparams["entity_version_1"]
        new_ver = cparams["entity_version"]

        row = self.rows.get((table_name, rid))
        if row and row["tenant_id"] == tid and row["entity_version"] < threshold:
            row["entity_version"] = new_ver
            for k in ("title", "status", "kind"):
                if k in cparams:
                    row[k] = cparams[k]
            return _MockResult(rid)
        return _MockResult(None)


@pytest.mark.parametrize(
    ("table_name", "provider"),
    [
        ("pull_requests", "github"),
        ("issues", "jira"),
        ("communication_events", "slack"),
    ],
)
@pytest.mark.asyncio
async def test_execute_versioned_update_increments_metric_only_on_zero_row_stale(
    table_name: str,
    provider: str,
) -> None:
    db = _MockVersionedSession()
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    db.seed(table_name, rid, tid, ver=1000)

    before = {p: _metric_sample(p) for p in ("github", "jira", "slack")}

    # Newer version -> 1 row updated -> metric must NOT increment
    res_new = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        incoming_version=2000,
        values={"title": "Newer"} if table_name != "communication_events" else {"kind": "resolved"},
    )
    assert res_new.applied is True
    assert _metric_sample(provider) == before[provider]

    # Delayed older version -> 0 rows updated -> metric increments by +1
    res_stale = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        incoming_version=1500,
        values={"title": "Old"} if table_name != "communication_events" else {"kind": "stale"},
    )
    assert res_stale.applied is False
    assert res_stale.stale_rejected is True
    assert _metric_sample(provider) == before[provider] + 1.0

    # Equal version -> 0 rows updated -> metric increments by +1 again
    res_dup = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        incoming_version=2000,
        values={"title": "Dup"} if table_name != "communication_events" else {"kind": "dup"},
    )
    assert res_dup.applied is False
    assert _metric_sample(provider) == before[provider] + 2.0

    # Other providers stayed untouched
    for other in ("github", "jira", "slack"):
        if other != provider:
            assert _metric_sample(other) == before[other]


@pytest.mark.asyncio
async def test_github_terminal_merged_pr_early_stale_increments_counter_once() -> None:
    tid = uuid.uuid4()
    repo = Repository(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=uuid.uuid4(),
        external_repo_id=4242,
        full_name="acme/svc",
    )
    existing_pr = PullRequest(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=repo.project_id,
        repository_id=repo.id,
        author_pseudonym_id="pseudo-1",
        external_pr_number=12,
        title="Merged PR",
        state="MERGED",
        head_branch="feat/x",
        base_branch="main",
        head_sha="b" * 40,
        entity_version=2_000_000_000_000,
        created_in_git_at=datetime.now(timezone.utc),
    )

    class _MockPRSession:
        def __init__(self) -> None:
            self._calls = 0

        async def execute(self, stmt: Any) -> _MockResult:
            self._calls += 1
            if self._calls == 1:
                return _MockResult(repo)
            return _MockResult(existing_pr)

    db = _MockPRSession()
    n = normalize_pull_request(
        {
            "action": "synchronize",
            "repository": {"id": 4242, "full_name": "acme/svc"},
            "pull_request": {
                "number": 12,
                "state": "open",
                "title": "Delayed Sync",
                "head": {"ref": "feat/x", "sha": "a" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octo"},
                "updated_at": "2026-11-19T10:00:00Z",
            },
        }
    )

    before_gh = _metric_sample("github")
    res = await process_pull_request(db, tenant_id=tid, n=n)  # type: ignore[arg-type]
    assert res == "noop:stale_pr_event:12"
    assert _metric_sample("github") == before_gh + 1.0


@pytest.mark.asyncio
async def test_jira_early_changelog_guard_and_cross_domain_guard_increment_once() -> None:
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()
    issue_row = Issue(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=proj_id,
        external_key="MANTA-243",
        issue_type="Story",
        title="Newer State",
        status="Done",
        changelog_id=205,
        entity_version=205,
    )

    # 1. Early in-memory guard: changelog.id=204 <= 205
    class _MockEarlyJiraSession:
        async def execute(self, stmt: Any) -> _MockResult:
            return _MockResult(issue_row)

    ev_204 = normalize_jira_event(
        "jira:issue_updated",
        {
            "webhookEvent": "jira:issue_updated",
            "changelog": {"id": "204", "items": []},
            "issue": {
                "key": "MANTA-243",
                "fields": {"summary": "Stale", "status": {"name": "In Progress"}},
            },
        },
    )
    assert ev_204.issue is not None
    before_jira = _metric_sample("jira")
    returned_issue, applied = await upsert_issue(
        _MockEarlyJiraSession(),  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        n=ev_204.issue,
    )
    assert applied is False
    assert returned_issue is issue_row
    assert _metric_sample("jira") == before_jira + 1.0

    # 2. Cross-domain guard: delayed jira:issue_created (changelog_id=None, epoch ms=1.795e12)
    #    must NOT overwrite an issue already holding numeric changelog_id=205.
    captured_sql: list[str] = []

    class _MockCrossDomainJiraSession:
        def __init__(self) -> None:
            self._calls = 0

        async def execute(self, stmt: Any) -> _MockResult:
            self._calls += 1
            if self._calls == 1:
                return _MockResult(issue_row)
            compiled_sql = str(stmt.compile())
            captured_sql.append(compiled_sql)
            # Simulate PostgreSQL evaluating `issues.changelog_id IS NULL AND ...` -> 0 rows!
            return _MockResult(None)

    ev_created_late = normalize_jira_event(
        "jira:issue_created",
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "key": "MANTA-243",
                "fields": {
                    "summary": "Delayed Initial Title",
                    "status": {"name": "To Do"},
                    "updated": "2026-11-19T10:00:00.000+0000",
                },
            },
        },
    )
    assert ev_created_late.issue is not None
    assert ev_created_late.issue.incoming_version > 1_000_000_000_000

    _, applied_late = await upsert_issue(
        _MockCrossDomainJiraSession(),  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        n=ev_created_late.issue,
    )
    assert applied_late is False
    assert captured_sql, "Expected conditional UPDATE SQL statement to be executed"
    assert "issues.changelog_id IS NULL" in captured_sql[0]
    assert _metric_sample("jira") == before_jira + 2.0


@pytest.mark.asyncio
async def test_slack_upsert_communication_event_versioned_increments_on_zero_row_mock() -> None:
    """Verify `upsert_communication_event_versioned` increments slack metric only on 0-row conflict."""
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()
    comm_id = uuid.uuid4()

    class _MockSlackSession:
        def __init__(self) -> None:
            self.stored_version = 0

        async def execute(self, stmt: Any) -> _MockResult:
            compiled = stmt.compile()
            incoming = compiled.params["entity_version"]
            if self.stored_version == 0 or self.stored_version < incoming:
                self.stored_version = incoming
                return _MockResult(comm_id)
            return _MockResult(None)

    db = _MockSlackSession()
    before_slack = _metric_sample("slack")

    res_new = await upsert_communication_event_versioned(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        integration_id=None,
        channel_id="C-MOCK",
        thread_ts="1700000001.000001",
        incoming_version=1700000010000200,
        is_resolved=True,
    )
    assert res_new.applied is True
    assert _metric_sample("slack") == before_slack

    res_stale = await upsert_communication_event_versioned(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        integration_id=None,
        channel_id="C-MOCK",
        thread_ts="1700000001.000001",
        incoming_version=1700000005000100,
        is_resolved=False,
    )
    assert res_stale.applied is False
    assert res_stale.stale_rejected is True
    assert _metric_sample("slack") == before_slack + 1.0


# ==============================================================================
# 4. Worker + DLQ Safety Tests (Zero Exceptions, Zero Retries, Zero DLQ)
# ==============================================================================


class _FakeWorkerRedis:
    """Minimal async Redis matching workers/worker.py & workers/retry.py contracts."""

    supports_lua = False

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)

    async def rpush(self, key: str, *values: str) -> int:
        self.lists[key].extend(values)
        return len(self.lists[key])

    async def _move(self, src_key: str, dest_key: str, side_s: str, side_d: str) -> str | None:
        if not self.lists[src_key]:
            return None
        item = self.lists[src_key].pop(0 if side_s == "LEFT" else -1)
        if side_d == "LEFT":
            self.lists[dest_key].insert(0, item)
        else:
            self.lists[dest_key].append(item)
        return item

    async def lmove(
        self, src_key: str, dest_key: str, src: str = "LEFT", dest: str = "RIGHT"
    ) -> str | None:
        return await self._move(src_key, dest_key, src, dest)

    async def blmove(
        self,
        src_key: str,
        dest_key: str,
        timeout: float = 0,
        src: str = "LEFT",
        dest: str = "RIGHT",
    ) -> str | None:
        return await self._move(src_key, dest_key, src, dest)

    async def lrem(self, key: str, count: int, value: str) -> int:
        if value in self.lists[key]:
            self.lists[key].remove(value)
            return 1
        return 0

    async def zrangebyscore(
        self, key: str, mn: Any, mx: Any, start: int = 0, num: int | None = None
    ) -> list[str]:
        return []


@pytest.mark.parametrize("provider", ["github", "jira", "slack"])
@pytest.mark.asyncio
async def test_worker_completes_stale_event_without_dlq_or_retry_and_increments_metric(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Acceptance Criteria (SCRUM-424):
    Stale events complete without raising exceptions (never sent to DLQ or retry queue)
    and increment `dropped_stale_event_total{provider=...}` by 1.
    """
    from workers import worker as worker_mod

    dlq_calls: list[Any] = []

    async def _forbid_failure_handler(*args: Any, **kwargs: Any) -> Any:
        dlq_calls.append((args, kwargs))
        raise AssertionError("handle_job_failure must NEVER be invoked for stale events")

    monkeypatch.setattr(worker_mod, "handle_job_failure", _forbid_failure_handler)

    redis = _FakeWorkerRedis()
    worker_id = f"worker-stale-test-{provider}"
    tenant_id = uuid.uuid4()
    event_id = uuid.uuid4()

    envelope = serialize_job_payload(
        job_id=event_id,
        tenant_id=tenant_id,
        priority="default",
        job_type="webhook.process",
        payload={"event_id": str(event_id), "tenant_id": str(tenant_id)},
        event_id=event_id,
        provider=provider,
    )
    await enqueue_job(redis, envelope, priority="default")

    before = _metric_sample(provider)

    async def _stale_job_handler(parsed_payload: dict[str, Any]) -> str:
        # Simulate Task 24.2 0-row stale update returning cleanly without raising
        record_dropped_stale_event(provider)
        return f"noop:stale_{provider}"

    failures: list[Any] = []
    processed = await run_worker_fetch_loop(
        redis,
        worker_id,
        _stale_job_handler,
        poll_timeout=0.0,
        max_iterations=1,
        ack_on_success=True,
        retry_on_failure=True,
        failures=failures,
    )

    assert len(processed) == 1
    assert failures == []
    assert dlq_calls == []
    assert redis.lists[QUEUE_DEFAULT] == []
    assert redis.lists[get_in_flight_key(worker_id)] == []
    assert _metric_sample(provider) == before + 1.0


# ==============================================================================
# 5. Live PostgreSQL E2E Tests (GitHub, Jira, Slack via WebhookEvent Processor)
# ==============================================================================


@pytest.mark.asyncio
async def test_live_pg_e2e_stale_events_preserve_state_complete_without_dlq_and_increment_metrics() -> (
    None
):
    """
    Full live PostgreSQL verification under tenant RLS using
    `process_staged_deployment_event_in_session`:
    1. GitHub PR: T+10m then delayed T+2m -> preserved T+10m state, +1 github metric.
    2. Jira Issue: changelog.id=205 (Done) then delayed 204 (In Progress) -> preserved Done/205, +1 jira metric.
       Plus delayed jira:issue_created (no changelog.id) -> preserved Done/205, +1 jira metric.
    3. Slack CommunicationEvent: 1700000010.000200 (resolved) then 1700000005.000100 (open) -> preserved resolved, +1 slack metric.
    4. All WebhookEvent rows marked 'completed' and 0 rows in `dead_letter_events`.
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host.")

    tid = await _provision_test_tenant("scrum424")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    slack_integ_id = uuid.uuid4()
    jira_integ_id = uuid.uuid4()

    async with get_tenant_db_context(tid) as db:
        db.add(
            Project(
                id=proj_id,
                tenant_id=tid,
                name="SCRUM-424 E2E Project",
                slug=f"s424-{proj_id.hex[:6]}",
            )
        )
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name="acme/scrum424-repo",
            )
        )
        db.add(
            Integration(
                id=jira_integ_id,
                tenant_id=tid,
                project_id=proj_id,
                provider="jira",
                external_account_id="jira-424",
                is_active=True,
            )
        )
        db.add(
            Integration(
                id=slack_integ_id,
                tenant_id=tid,
                project_id=proj_id,
                provider="slack",
                external_account_id="slack-424",
                is_active=True,
            )
        )

    async def _stage_and_process(
        *,
        provider: str,
        event_type: str,
        payload: dict[str, Any],
        integration_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, str]:
        ev_id = uuid.uuid4()
        async with get_tenant_db_context(tid) as db:
            db.add(
                WebhookEvent(
                    id=ev_id,
                    tenant_id=tid,
                    integration_id=integration_id,
                    provider=provider,
                    provider_delivery_id=f"{provider}-{ev_id.hex}",
                    event_type=event_type,
                    payload=payload,
                    status="staged",
                )
            )
        async with get_tenant_db_context(tid) as db:
            res = await process_staged_deployment_event_in_session(
                db,
                event_id=ev_id,
                tenant_id=tid,
            )
        return ev_id, res

    before = {p: _metric_sample(p) for p in ("github", "jira", "slack")}

    # 1. GitHub PR: newer T+10m, then delayed T+2m
    gh_ev1, gh_res1 = await _stage_and_process(
        provider="github",
        event_type="pull_request",
        payload={
            "action": "synchronize",
            "repository": {"full_name": "acme/scrum424-repo"},
            "pull_request": {
                "number": 424,
                "title": "Authoritative T+10m PR Title",
                "state": "open",
                "head": {"ref": "feat/424", "sha": "b" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octo"},
                "created_at": "2026-11-19T10:00:00Z",
                "updated_at": "2026-11-19T10:10:00Z",
            },
        },
    )
    assert gh_res1.startswith("persisted_pr:")
    assert _metric_sample("github") == before["github"]

    gh_ev2, gh_res2 = await _stage_and_process(
        provider="github",
        event_type="pull_request",
        payload={
            "action": "synchronize",
            "repository": {"full_name": "acme/scrum424-repo"},
            "pull_request": {
                "number": 424,
                "title": "Stale T+2m PR Title",
                "state": "open",
                "head": {"ref": "feat/424", "sha": "a" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octo"},
                "created_at": "2026-11-19T10:00:00Z",
                "updated_at": "2026-11-19T10:02:00Z",
            },
        },
    )
    assert gh_res2 == "noop:stale_pr_version:424"
    assert _metric_sample("github") == before["github"] + 1.0

    # 2. Jira Issue: changelog.id=205 (Done), then delayed changelog.id=204 (In Progress)
    jira_ev1, jira_res1 = await _stage_and_process(
        provider="jira",
        event_type="jira:issue_updated",
        integration_id=jira_integ_id,
        payload={
            "webhookEvent": "jira:issue_updated",
            "changelog": {"id": "205", "items": []},
            "issue": {
                "id": "42401",
                "key": "MANTA-424",
                "fields": {
                    "summary": "Done at 205",
                    "status": {"name": "Done"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:10:00.000+0000",
                },
            },
        },
    )
    assert jira_res1.startswith("persisted_issue:")
    assert _metric_sample("jira") == before["jira"]

    jira_ev2, jira_res2 = await _stage_and_process(
        provider="jira",
        event_type="jira:issue_updated",
        integration_id=jira_integ_id,
        payload={
            "webhookEvent": "jira:issue_updated",
            "changelog": {"id": "204", "items": []},
            "issue": {
                "id": "42401",
                "key": "MANTA-424",
                "fields": {
                    "summary": "Stale In Progress at 204",
                    "status": {"name": "In Progress"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:02:00.000+0000",
                },
            },
        },
    )
    assert jira_res2 == "noop:stale_issue_version:MANTA-424"
    assert _metric_sample("jira") == before["jira"] + 1.0

    # Delayed jira:issue_created without changelog.id must ALSO be rejected against changelog_id=205
    jira_ev3, jira_res3 = await _stage_and_process(
        provider="jira",
        event_type="jira:issue_created",
        integration_id=jira_integ_id,
        payload={
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "42401",
                "key": "MANTA-424",
                "fields": {
                    "summary": "Delayed Initial Summary",
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:00:00.000+0000",
                },
            },
        },
    )
    assert jira_res3 == "noop:stale_issue_version:MANTA-424"
    assert _metric_sample("jira") == before["jira"] + 2.0

    # 3. Slack CommunicationEvent: 1700000010.000200 (resolved) then 1700000005.000100 (open)
    slack_ev1, slack_res1 = await _stage_and_process(
        provider="slack",
        event_type="event_callback",
        integration_id=slack_integ_id,
        payload={
            "type": "event_callback",
            "kind": "blocker_resolved",
            "is_resolved": True,
            "event": {
                "type": "message",
                "channel": "C424",
                "thread_ts": "1700000001.000001",
                "ts": "1700000010.000200",
                "event_ts": "1700000010.000200",
            },
        },
    )
    assert slack_res1.startswith("persisted_communication_event:")
    assert _metric_sample("slack") == before["slack"]

    slack_ev2, slack_res2 = await _stage_and_process(
        provider="slack",
        event_type="event_callback",
        integration_id=slack_integ_id,
        payload={
            "type": "event_callback",
            "kind": "blocker_open",
            "is_resolved": False,
            "event": {
                "type": "message",
                "channel": "C424",
                "thread_ts": "1700000001.000001",
                "ts": "1700000005.000100",
                "event_ts": "1700000005.000100",
            },
        },
    )
    assert slack_res2 == "noop:stale_slack_event:C424:1700000001.000001"
    assert _metric_sample("slack") == before["slack"] + 1.0

    # Verify DB invariants: newer states preserved, all events completed, 0 DLQ entries
    async with get_tenant_db_context(tid) as db:
        pr_row = (
            await db.execute(
                select(PullRequest).where(
                    PullRequest.tenant_id == tid,
                    PullRequest.external_pr_number == 424,
                )
            )
        ).scalar_one()
        assert pr_row.title == "Authoritative T+10m PR Title"
        assert pr_row.head_sha == "b" * 40

        issue_row = (
            await db.execute(
                select(Issue).where(
                    Issue.tenant_id == tid,
                    Issue.external_key == "MANTA-424",
                )
            )
        ).scalar_one()
        assert issue_row.status == "Done"
        assert issue_row.title == "Done at 205"
        assert issue_row.changelog_id == 205
        assert issue_row.entity_version == 205

        comm_row = (
            await db.execute(
                select(CommunicationEvent).where(
                    CommunicationEvent.tenant_id == tid,
                    CommunicationEvent.channel_id == "C424",
                    CommunicationEvent.thread_ts == "1700000001.000001",
                )
            )
        ).scalar_one()
        assert comm_row.is_resolved is True
        assert comm_row.entity_version == 1700000010000200

        statuses = (
            (
                await db.execute(
                    select(WebhookEvent.status).where(
                        WebhookEvent.id.in_(
                            [gh_ev1, gh_ev2, jira_ev1, jira_ev2, jira_ev3, slack_ev1, slack_ev2]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(statuses) == 7
        assert all(s == "completed" for s in statuses)

        dlq_count = (
            await db.execute(
                select(func.count(DeadLetterEvent.id)).where(DeadLetterEvent.tenant_id == tid)
            )
        ).scalar_one()
        assert dlq_count == 0
