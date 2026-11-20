"""
SCRUM-361 (US-24): Out-of-Order Webhook Delivery Protection & Monotonic Versioning.
SCRUM-426 (Task 24.5): Write automated pytest tests verifying stale updates are ignored
and dropped_stale_event_total increments by 1 per provider.

Parent User Story: US-24 / SCRUM-361
Related Tasks:
- SCRUM-422 (Task 24.1): Canonical monotonic version extraction rules for GitHub, Jira, and Slack.
- SCRUM-423 (Task 24.2): Conditional PostgreSQL update guards and monotonic entity_version column.
- SCRUM-424 (Task 24.3): dropped_stale_event_total{provider="github"|"jira"|"slack"} Prometheus counter.
- SCRUM-425 (Task 24.4): Out-of-order webhook delivery protection test scenarios.
- SCRUM-426 (Task 24.5): Automated pytest tests executing all 3 provider out-of-order sequences,
  asserting state preservation and dropped_stale_event_total increments.

Automated Test Scenarios Covered:
1. TC-OOO-GH-001: GitHub PR out-of-order synchronize (T+10m applied, T+2m rejected, state preserved, github +1).
2. TC-OOO-GH-002: GitHub PR terminal state protection on MERGED PR (reopened/sync rejected, state preserved, github +1).
3. TC-OOO-JIRA-001: Jira issue out-of-order changelog (changelog.id 205 applied, 204 rejected, state preserved, jira +1).
4. TC-OOO-JIRA-002: Jira delayed issue_created without changelog after update (cross-domain guard, state preserved, jira +1).
5. TC-OOO-SLACK-001: Slack communication event out-of-order thread update (resolved applied, older unresolve rejected, state preserved, slack +1).
6. TC-OOO-SLACK-002: Slack microsecond Decimal precision drift defense (exact microsecond ordering, slack +1).
7. TC-OOO-IDENT-001: Identical entity version duplicate drop across all 3 providers (< predicate rejects duplicate, state preserved, provider +1).
8. TC-OOO-XTEN-001: Multi-tenant RLS out-of-order independence (tenant A stale drop does not affect tenant B in-order delivery).
9. TC-OOO-WORKER-001: Worker fetch loop zero-DLQ and clean NOOP execution (stale job acknowledged, 0 DLQ writes, provider +1).
10. TC-OOO-PROM-001: Prometheus /metrics public exposition, exact label cardinality, and strict validation.
11. TC-OOO-LIVE-001: Full Live PostgreSQL end-to-end multi-provider out-of-order execution pipeline under tenant RLS.
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
    """Reads the current dropped_stale_event_total sample value for a given provider label."""
    val = REGISTRY.get_sample_value("dropped_stale_event_total", {"provider": provider})
    assert val is not None, f"Metric dropped_stale_event_total{{provider={provider!r}}} missing"
    return float(val)


async def _is_live_pg_ready() -> bool:
    """Checks whether the live PostgreSQL database instance is reachable."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception:
        return False


async def _provision_test_tenant(slug_prefix: str) -> uuid.UUID:
    """Provisions an isolated test tenant using the provision_tenant SQL procedure."""
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


class _MockResult:
    """Mock query result helper returning scalars or lists."""

    def __init__(self, val: Any) -> None:
        self._val = val

    def scalar_one_or_none(self) -> Any:
        return self._val

    def scalar_one(self) -> Any:
        return self._val

    def scalars(self) -> _MockResult:
        return self

    def all(self) -> list[Any]:
        if self._val is None:
            return []
        if isinstance(self._val, list):
            return self._val
        return [self._val]


class _MockPRSession:
    """
    High-fidelity in-memory AsyncSession simulator for GitHub Pull Request processing.
    Simulates repository resolution, existing PR retrieval, and versioned UPDATE execution.
    """

    def __init__(self, repo: Repository, existing_pr: PullRequest | None = None) -> None:
        self.repo = repo
        self.pr = existing_pr
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, PullRequest):
            self.pr = obj

    async def execute(self, stmt: Any) -> _MockResult:
        stmt_str = str(stmt)
        if "FROM repositories" in stmt_str:
            return _MockResult(self.repo)
        if "FROM pull_requests" in stmt_str:
            return _MockResult(self.pr)
        if "UPDATE pull_requests" in stmt_str:
            compiled = stmt.compile()
            cparams = compiled.params
            incoming_ver = cparams.get("entity_version")
            threshold = cparams.get("entity_version_1")
            if (
                self.pr is not None
                and threshold is not None
                and incoming_ver is not None
                and self.pr.entity_version is not None
                and self.pr.entity_version < incoming_ver
            ):
                self.pr.entity_version = incoming_ver
                if "title" in cparams:
                    self.pr.title = cparams["title"]
                if "head_sha" in cparams:
                    self.pr.head_sha = cparams["head_sha"]
                if "state" in cparams:
                    self.pr.state = cparams["state"]
                return _MockResult(self.pr.id)
            return _MockResult(None)
        if "FROM commits" in stmt_str:
            return _MockResult([])
        return _MockResult(None)

    async def refresh(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass


class _MockJiraSession:
    """
    High-fidelity in-memory AsyncSession simulator for Jira issue upsert processing.
    Simulates issue lookup and conditional UPDATE execution with changelog precedence guards.
    """

    def __init__(self, existing_issue: Issue | None = None) -> None:
        self.issue = existing_issue
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, Issue):
            self.issue = obj

    async def execute(self, stmt: Any) -> _MockResult:
        stmt_str = str(stmt)
        if "FROM issues" in stmt_str:
            return _MockResult(self.issue)
        if "UPDATE issues" in stmt_str:
            compiled = stmt.compile()
            cparams = compiled.params
            incoming_ver = cparams.get("entity_version")
            incoming_cl = cparams.get("changelog_id")
            # If existing issue already has a changelog_id, a payload without changelog
            # (where incoming_cl is None) fails the `issues.changelog_id IS NULL` check.
            if (
                self.issue is not None
                and self.issue.changelog_id is not None
                and incoming_cl is None
            ):
                return _MockResult(None)
            if (
                self.issue is not None
                and incoming_ver is not None
                and self.issue.entity_version is not None
                and self.issue.entity_version < incoming_ver
            ):
                self.issue.entity_version = incoming_ver
                if incoming_cl is not None:
                    self.issue.changelog_id = incoming_cl
                if "title" in cparams:
                    self.issue.title = cparams["title"]
                if "status" in cparams:
                    self.issue.status = cparams["status"]
                return _MockResult(self.issue.id)
            return _MockResult(None)
        if "FROM sprints" in stmt_str:
            return _MockResult([])
        return _MockResult(None)

    async def flush(self) -> None:
        pass


class _MockSlackSession:
    """
    High-fidelity in-memory AsyncSession simulator for Slack communication event upsert.
    Simulates `ON CONFLICT DO UPDATE WHERE entity_version < EXCLUDED.entity_version`.
    """

    def __init__(self, initial_version: int = 0, initial_resolved: bool = True) -> None:
        self.comm_id = uuid.uuid4()
        self.stored_version = initial_version
        self.is_resolved = initial_resolved
        self.kind = "blocker_resolved" if initial_resolved else "blocker_open"

    async def execute(self, stmt: Any) -> _MockResult:
        compiled = stmt.compile()
        cparams = compiled.params
        incoming_version = cparams.get("entity_version")
        incoming_resolved = cparams.get("is_resolved")
        incoming_kind = cparams.get("kind")
        if incoming_version is not None:
            if self.stored_version == 0 or self.stored_version < incoming_version:
                self.stored_version = incoming_version
                if incoming_resolved is not None:
                    self.is_resolved = incoming_resolved
                if incoming_kind is not None:
                    self.kind = incoming_kind
                return _MockResult(self.comm_id)
        return _MockResult(None)

    async def flush(self) -> None:
        pass


class _MockVersionedUpdateSession:
    """In-memory mock session simulating `execute_versioned_update` across tables."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}

    def seed(self, table: str, rid: uuid.UUID, tid: uuid.UUID, ver: int, title: str) -> None:
        self.rows[(table, rid)] = {"tenant_id": tid, "entity_version": ver, "title": title}

    async def execute(self, stmt: Any) -> _MockResult:
        compiled = stmt.compile()
        cparams = compiled.params
        table_name = stmt.table.name
        rid = cparams.get("id_1")
        tid = cparams.get("tenant_id_1")
        threshold = cparams.get("entity_version_1")
        new_ver = cparams.get("entity_version")

        row = self.rows.get((table_name, rid))
        if (
            row
            and row["tenant_id"] == tid
            and threshold is not None
            and new_ver is not None
            and row["entity_version"] < threshold
        ):
            row["entity_version"] = new_ver
            for k in ("title", "status", "kind"):
                if k in cparams:
                    row[k] = cparams[k]
            return _MockResult(rid)
        return _MockResult(None)


class _FakeWorkerRedis:
    """In-memory async Redis double for worker fetch loop & DLQ verification."""

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


# ==============================================================================
# 1. GitHub Out-of-Order Webhook Sequences (TC-OOO-GH-001 & TC-OOO-GH-002)
# ==============================================================================


@pytest.mark.asyncio
async def test_github_pr_out_of_order_synchronize_preserves_state_and_increments_metric() -> None:
    """
    TC-OOO-GH-001:
    A newer GitHub PR synchronize event arrives at T+10m (head_sha = b*40) and applies.
    A delayed older synchronize event arrives at T+2m (head_sha = a*40).
    Assert:
    1. T+2m event returns clean NOOP 'noop:stale_pr_version:426'.
    2. Stored PR row preserves T+10m state ('Authoritative T+10m PR Title', head_sha = b*40).
    3. dropped_stale_event_total{provider="github"} increments by exactly +1.0.
    4. Counters for 'jira' and 'slack' do NOT increment.
    """
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()

    repo = Repository(
        id=repo_id,
        tenant_id=tid,
        project_id=proj_id,
        external_repo_id=426426,
        full_name="acme/scrum426-repo",
    )

    t_10m = "2026-11-19T10:10:00Z"
    t_2m = "2026-11-19T10:02:00Z"
    t_10m_epoch_ms = parse_timestamp_to_epoch_ms(t_10m)
    assert t_10m_epoch_ms is not None

    existing_pr = PullRequest(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=proj_id,
        repository_id=repo_id,
        author_pseudonym_id="pseudo-gh-426",
        external_pr_number=426,
        title="Authoritative T+10m PR Title",
        state="open",
        head_branch="feat/426",
        base_branch="main",
        head_sha="b" * 40,
        entity_version=t_10m_epoch_ms,
        created_in_git_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 11, 19, 10, 10, 0, tzinfo=timezone.utc),
    )

    db = _MockPRSession(repo, existing_pr)

    before_gh = _metric_sample("github")
    before_jira = _metric_sample("jira")
    before_slack = _metric_sample("slack")

    # Delayed older synchronize payload (T+2m, head_sha = a*40)
    pr_older = normalize_pull_request(
        {
            "action": "synchronize",
            "repository": {"id": 426426, "full_name": "acme/scrum426-repo"},
            "pull_request": {
                "number": 426,
                "title": "Stale T+2m PR Title",
                "state": "open",
                "head": {"ref": "feat/426", "sha": "a" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octocat"},
                "created_at": "2026-11-19T10:00:00Z",
                "updated_at": t_2m,
            },
        }
    )

    res = await process_pull_request(db, tenant_id=tid, n=pr_older)  # type: ignore[arg-type]

    # Invariants verification
    assert res == "noop:stale_pr_version:426"
    assert db.pr is not None
    assert db.pr.title == "Authoritative T+10m PR Title"
    assert db.pr.head_sha == "b" * 40
    assert db.pr.entity_version == t_10m_epoch_ms

    # Exact-once metric deltas
    assert _metric_sample("github") == before_gh + 1.0
    assert _metric_sample("jira") == before_jira
    assert _metric_sample("slack") == before_slack


@pytest.mark.asyncio
async def test_github_pr_terminal_merged_guard_preserves_state_and_increments_metric() -> None:
    """
    TC-OOO-GH-002:
    A PR has already transitioned to terminal 'MERGED' state with merge_commit_sha.
    A delayed out-of-order 'synchronize' or 'opened' event arrives.
    Assert:
    1. Early terminal state guard rejects event, returning 'noop:stale_pr_event:426'.
    2. Stored PR state remains 'MERGED' and merge_commit_sha remains intact.
    3. dropped_stale_event_total{provider="github"} increments by +1.0.
    """
    tid = uuid.uuid4()
    repo = Repository(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=uuid.uuid4(),
        external_repo_id=426426,
        full_name="acme/scrum426-repo",
    )
    merged_pr = PullRequest(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=repo.project_id,
        repository_id=repo.id,
        author_pseudonym_id="pseudo-gh-426",
        external_pr_number=426,
        title="Authoritative Merged PR",
        state="MERGED",
        head_branch="feat/426",
        base_branch="main",
        head_sha="b" * 40,
        merge_commit_sha="c" * 40,
        entity_version=2_000_000_000_000,
        created_in_git_at=datetime.now(timezone.utc),
    )

    db = _MockPRSession(repo, merged_pr)

    n_sync = normalize_pull_request(
        {
            "action": "synchronize",
            "repository": {"id": 426426, "full_name": "acme/scrum426-repo"},
            "pull_request": {
                "number": 426,
                "state": "open",
                "title": "Delayed Sync on Merged PR",
                "head": {"ref": "feat/426", "sha": "a" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octocat"},
                "updated_at": "2026-11-19T10:00:00Z",
            },
        }
    )

    before_gh = _metric_sample("github")
    res = await process_pull_request(db, tenant_id=tid, n=n_sync)  # type: ignore[arg-type]

    assert res == "noop:stale_pr_event:426"
    assert db.pr is not None
    assert db.pr.state == "MERGED"
    assert db.pr.merge_commit_sha == "c" * 40
    assert _metric_sample("github") == before_gh + 1.0


# ==============================================================================
# 2. Jira Out-of-Order Webhook Sequences (TC-OOO-JIRA-001 & TC-OOO-JIRA-002)
# ==============================================================================


@pytest.mark.asyncio
async def test_jira_issue_out_of_order_changelog_preserves_state_and_increments_metric() -> None:
    """
    TC-OOO-JIRA-001:
    Jira issue moves to 'In Progress' at T+2m (changelog.id = 204), then 'Done' at T+10m (changelog.id = 205).
    Authoritative changelog.id = 205 commits first.
    Delayed changelog.id = 204 arrives afterwards.
    Assert:
    1. Early monotonic changelog check (204 <= 205) rejects mutation and returns applied=False.
    2. Stored issue preserves state: status='Done', title='Done at 205', changelog_id=205.
    3. dropped_stale_event_total{provider="jira"} increments by exactly +1.0.
    4. Counters for 'github' and 'slack' do NOT increment.
    """
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()

    stored_issue = Issue(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=proj_id,
        external_key="MANTA-426",
        issue_type="Story",
        title="Done at 205",
        status="Done",
        changelog_id=205,
        entity_version=205,
    )

    db = _MockJiraSession(stored_issue)

    before_jira = _metric_sample("jira")
    before_gh = _metric_sample("github")
    before_slack = _metric_sample("slack")

    # Stale changelog.id = 204 payload
    ev_204 = normalize_jira_event(
        "jira:issue_updated",
        {
            "webhookEvent": "jira:issue_updated",
            "changelog": {
                "id": "204",
                "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}],
            },
            "issue": {
                "id": "42601",
                "key": "MANTA-426",
                "fields": {
                    "summary": "Stale In Progress at 204",
                    "status": {"name": "In Progress"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:02:00.000+0000",
                },
            },
        },
    )
    assert ev_204.issue is not None

    returned_issue, applied = await upsert_issue(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        n=ev_204.issue,
    )

    assert applied is False
    assert returned_issue is stored_issue
    assert returned_issue.status == "Done"
    assert returned_issue.title == "Done at 205"
    assert returned_issue.changelog_id == 205
    assert returned_issue.entity_version == 205

    assert _metric_sample("jira") == before_jira + 1.0
    assert _metric_sample("github") == before_gh
    assert _metric_sample("slack") == before_slack


@pytest.mark.asyncio
async def test_jira_issue_cross_domain_delayed_creation_rejected_and_increments_metric() -> None:
    """
    TC-OOO-JIRA-002:
    An issue has already been updated with authoritative changelog_id = 205.
    A delayed initial jira:issue_created event (no changelog.id, timestamp fallback = 1.795e12 ms) arrives.
    Assert:
    1. Cross-domain SQL guard (Issue.changelog_id IS NULL) evaluates to FALSE.
    2. 0 rows updated, returns applied=False.
    3. Stored issue remains status='Done', changelog_id=205.
    4. dropped_stale_event_total{provider="jira"} increments by +1.0.
    """
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()

    stored_issue = Issue(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=proj_id,
        external_key="MANTA-426",
        issue_type="Story",
        title="Done at 205",
        status="Done",
        changelog_id=205,
        entity_version=205,
    )

    db = _MockJiraSession(stored_issue)

    before_jira = _metric_sample("jira")

    ev_created_late = normalize_jira_event(
        "jira:issue_created",
        {
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "42601",
                "key": "MANTA-426",
                "fields": {
                    "summary": "Delayed Initial Summary",
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:00:00.000+0000",
                },
            },
        },
    )
    assert ev_created_late.issue is not None
    assert ev_created_late.issue.changelog_id is None
    assert ev_created_late.issue.incoming_version > 1_000_000_000_000

    returned_issue, applied = await upsert_issue(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        n=ev_created_late.issue,
    )

    assert applied is False
    assert returned_issue.status == "Done"
    assert returned_issue.changelog_id == 205
    assert _metric_sample("jira") == before_jira + 1.0


# ==============================================================================
# 3. Slack Out-of-Order Webhook Sequences (TC-OOO-SLACK-001 & TC-OOO-SLACK-002)
# ==============================================================================


@pytest.mark.asyncio
async def test_slack_communication_event_out_of_order_thread_preserves_state_and_increments_metric() -> (
    None
):
    """
    TC-OOO-SLACK-001:
    A Slack blocker thread in channel C426 is resolved at event_ts = 1700000010.000200 (is_resolved = True).
    A delayed unresolve event at event_ts = 1700000005.000100 (is_resolved = False) arrives out-of-order.
    Assert:
    1. ON CONFLICT WHERE entity_version < EXCLUDED.entity_version modifies 0 rows.
    2. upsert_communication_event_versioned returns applied=False, stale_rejected=True.
    3. Stored state preserves is_resolved=True, entity_version=1700000010000200.
    4. dropped_stale_event_total{provider="slack"} increments by exactly +1.0.
    5. Counters for 'github' and 'jira' do NOT increment.
    """
    tid = uuid.uuid4()
    proj_id = uuid.uuid4()

    newer_ver = parse_slack_ts_to_micros("1700000010.000200")
    stale_ver = parse_slack_ts_to_micros("1700000005.000100")
    assert newer_ver == 1700000010000200
    assert stale_ver == 1700000005000100

    # Start with newer authoritative version stored
    db = _MockSlackSession(initial_version=newer_ver, initial_resolved=True)

    before_slack = _metric_sample("slack")
    before_gh = _metric_sample("github")
    before_jira = _metric_sample("jira")

    # Delayed older event arrives
    res_stale = await upsert_communication_event_versioned(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=proj_id,
        integration_id=None,
        channel_id="C426",
        thread_ts="1700000001.000001",
        incoming_version=stale_ver,
        kind="blocker_open",
        is_resolved=False,
    )

    assert res_stale.applied is False
    assert res_stale.stale_rejected is True
    assert db.stored_version == newer_ver
    assert db.is_resolved is True
    assert db.kind == "blocker_resolved"

    assert _metric_sample("slack") == before_slack + 1.0
    assert _metric_sample("github") == before_gh
    assert _metric_sample("jira") == before_jira


@pytest.mark.asyncio
async def test_slack_microsecond_decimal_precision_drift_defense() -> None:
    """
    TC-OOO-SLACK-002:
    Proves Slack timestamp extraction uses Decimal to avoid IEEE-754 precision drift.
    Version 1731326400.123456 (1731326400123456) is followed by delayed 1731326400.123455 (1 microsecond earlier).
    Assert:
    1. 1 microsecond earlier event is recognized as strictly older (1731326400123455 < 1731326400123456).
    2. Mutation rejected, applied=False, stale_rejected=True.
    3. dropped_stale_event_total{provider="slack"} increments by +1.0.
    """
    # Decimal string verification
    raw_ts = "1731326400.123456"
    assert Decimal(raw_ts) == Decimal("1731326400.123456")
    assert extract_entity_version("slack", {"event": {"ts": raw_ts}}) == 1731326400123456

    v_head = parse_slack_ts_to_micros(raw_ts)
    v_stale = parse_slack_ts_to_micros("1731326400.123455")
    assert v_head == 1731326400123456
    assert v_stale == 1731326400123455
    assert v_stale < v_head

    db = _MockSlackSession(initial_version=v_head, initial_resolved=True)
    before_slack = _metric_sample("slack")

    res = await upsert_communication_event_versioned(
        db,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        integration_id=None,
        channel_id="C426",
        thread_ts="1731326400.123456",
        incoming_version=v_stale,
        is_resolved=False,
    )

    assert res.applied is False
    assert res.stale_rejected is True
    assert db.stored_version == v_head
    assert _metric_sample("slack") == before_slack + 1.0


# ==============================================================================
# 4. Identical Version Duplicate Drop (TC-OOO-IDENT-001)
# ==============================================================================


@pytest.mark.parametrize(
    ("table_name", "provider"),
    [
        ("pull_requests", "github"),
        ("issues", "jira"),
        ("communication_events", "slack"),
    ],
)
@pytest.mark.asyncio
async def test_identical_entity_version_duplicate_drop_across_all_providers(
    table_name: Any,
    provider: str,
) -> None:
    """
    TC-OOO-IDENT-001:
    A duplicate event with identical entity_version arrives for an already-applied record.
    The strict `< :incoming_version` condition evaluates to FALSE (v < v is False).
    Assert:
    1. 0 rows updated, applied=False, stale_rejected=True.
    2. Stored version and title preserved.
    3. dropped_stale_event_total{provider=...} increments by +1.0 for exact provider only.
    """
    db = _MockVersionedUpdateSession()
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    v_stored = 5000

    db.seed(table_name, rid, tid, ver=v_stored, title="Initial Title")

    before = {p: _metric_sample(p) for p in ("github", "jira", "slack")}

    update_val = (
        {"title": "Duplicate Overwrite"}
        if table_name != "communication_events"
        else {"kind": "duplicate_event"}
    )
    res_dup = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,
        record_id=rid,
        tenant_id=tid,
        incoming_version=v_stored,
        values=update_val,
    )

    assert res_dup.applied is False
    assert res_dup.stale_rejected is True
    assert db.rows[(table_name, rid)]["entity_version"] == v_stored
    if table_name != "communication_events":
        assert db.rows[(table_name, rid)]["title"] == "Initial Title"
    else:
        assert db.rows[(table_name, rid)]["title"] == "Initial Title"

    assert _metric_sample(provider) == before[provider] + 1.0
    for other in ("github", "jira", "slack"):
        if other != provider:
            assert _metric_sample(other) == before[other]


# ==============================================================================
# 5. Multi-Tenant RLS Out-of-Order Independence (TC-OOO-XTEN-001)
# ==============================================================================


@pytest.mark.asyncio
async def test_multi_tenant_rls_out_of_order_independence() -> None:
    """
    TC-OOO-XTEN-001:
    Verifies out-of-order rejection logic is strictly isolated by tenant_id.
    Tenant A receives newer then older event (delayed event dropped, +1 drop).
    Tenant B receives older then newer event (both accepted in sequence, 0 drops).
    Assert zero cross-tenant interference.
    """
    db = _MockVersionedUpdateSession()
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    rid_a = uuid.uuid4()
    rid_b = uuid.uuid4()

    # Seed both tenants with baseline version 1000
    db.seed("pull_requests", rid_a, tid_a, ver=1000, title="Initial A")
    db.seed("pull_requests", rid_b, tid_b, ver=1000, title="Initial B")

    before_gh = _metric_sample("github")

    # Tenant A receives newer (2000) then delayed older (1500)
    res_a1 = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name="pull_requests",
        record_id=rid_a,
        tenant_id=tid_a,
        incoming_version=2000,
        values={"title": "Newer A"},
    )
    assert res_a1.applied is True

    res_a2 = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name="pull_requests",
        record_id=rid_a,
        tenant_id=tid_a,
        incoming_version=1500,
        values={"title": "Stale A"},
    )
    assert res_a2.applied is False
    assert res_a2.stale_rejected is True
    assert db.rows[("pull_requests", rid_a)]["title"] == "Newer A"
    assert db.rows[("pull_requests", rid_a)]["entity_version"] == 2000

    # Tenant B receives 1500 then 2000 (both valid monotonic progression)
    res_b1 = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name="pull_requests",
        record_id=rid_b,
        tenant_id=tid_b,
        incoming_version=1500,
        values={"title": "Step 1 B"},
    )
    assert res_b1.applied is True

    res_b2 = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name="pull_requests",
        record_id=rid_b,
        tenant_id=tid_b,
        incoming_version=2000,
        values={"title": "Step 2 B"},
    )
    assert res_b2.applied is True
    assert db.rows[("pull_requests", rid_b)]["title"] == "Step 2 B"
    assert db.rows[("pull_requests", rid_b)]["entity_version"] == 2000

    # Exactly 1 drop recorded (Tenant A's stale event only)
    assert _metric_sample("github") == before_gh + 1.0


# ==============================================================================
# 6. Worker Queue Zero-DLQ & Clean NOOP Execution (TC-OOO-WORKER-001)
# ==============================================================================


@pytest.mark.parametrize("provider", ["github", "jira", "slack"])
@pytest.mark.asyncio
async def test_worker_fetch_loop_zero_dlq_and_clean_noop_execution(
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    TC-OOO-WORKER-001:
    A stale webhook job is pulled from the queue by the worker fetch loop.
    Assert:
    1. Job handler completes cleanly returning NOOP string without raising exceptions.
    2. handle_job_failure is NEVER invoked.
    3. Job is acknowledged (popped from in-flight queue).
    4. 0 retries and 0 rows written to dead_letter_events.
    5. dropped_stale_event_total{provider=...} increments by exactly +1.0.
    """
    from workers import worker as worker_mod

    dlq_calls: list[Any] = []

    async def _forbid_failure_handler(*args: Any, **kwargs: Any) -> Any:
        dlq_calls.append((args, kwargs))
        raise AssertionError("handle_job_failure must NEVER be called for stale webhook jobs")

    monkeypatch.setattr(worker_mod, "handle_job_failure", _forbid_failure_handler)

    redis = _FakeWorkerRedis()
    worker_id = f"worker-ooo-test-{provider}"
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
        record_dropped_stale_event(provider)
        return f"noop:stale_{provider}_version"

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
# 7. Prometheus Exposition and Metric Card (TC-OOO-PROM-001)
# ==============================================================================


@pytest.mark.asyncio
async def test_prometheus_metrics_exposition_and_provider_validation() -> None:
    """
    TC-OOO-PROM-001:
    Assert:
    1. Public GET /metrics exports dropped_stale_event_total counter for github, jira, slack.
    2. Pre-initialized counter values are readable via get_dropped_stale_event_count.
    3. Unsupported provider string raises ValueError (strict cardinality protection).
    4. Non-positive increment amounts raise ValueError.
    """
    assert DROPPED_STALE_EVENT_TOTAL is not None
    assert set(SUPPORTED_STALE_EVENT_PROVIDERS) == {"github", "jira", "slack"}
    for provider in ("github", "jira", "slack"):
        assert get_dropped_stale_event_count(provider) >= 0.0

    # Validation guards
    with pytest.raises(ValueError, match="Unsupported provider for dropped_stale_event_total"):
        record_dropped_stale_event("unsupported_provider")

    with pytest.raises(ValueError, match="amount must be a positive integer"):
        record_dropped_stale_event("github", amount=0)

    with pytest.raises(ValueError, match="amount must be a positive integer"):
        record_dropped_stale_event("slack", amount=-5)

    # Scrape /metrics via ASGI client
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "# TYPE dropped_stale_event_total counter" in body
        assert 'dropped_stale_event_total{provider="github"}' in body
        assert 'dropped_stale_event_total{provider="jira"}' in body
        assert 'dropped_stale_event_total{provider="slack"}' in body


# ==============================================================================
# 8. Comprehensive Live PostgreSQL Integration Test (All 3 Providers E2E)
# ==============================================================================


@pytest.mark.asyncio
async def test_live_pg_all_three_providers_out_of_order_pipeline() -> None:
    """
    TC-OOO-LIVE-001:
    Full live PostgreSQL E2E execution of out-of-order webhook delivery across
    GitHub, Jira, and Slack via `process_staged_deployment_event_in_session` under tenant RLS.

    Execution Flow:
    1. GitHub PR: Deliver T+10m (head_sha=b*40) -> applied. Deliver T+2m (head_sha=a*40) -> dropped.
    2. Jira Issue: Deliver changelog.id=205 (Done) -> applied. Deliver changelog.id=204 (In Progress) -> dropped.
       Deliver delayed issue_created without changelog -> dropped.
    3. Slack: Deliver event_ts=1700000010.000200 (Resolved) -> applied.
       Deliver event_ts=1700000005.000100 (Open) -> dropped.

    Assertions:
    1. Stored DB rows preserve authoritative newer states.
    2. All 7 staged WebhookEvent rows complete with status='completed'.
    3. dead_letter_events count is strictly 0.
    4. Exact Prometheus counter increments:
       - github: +1.0
       - jira: +2.0 (1 for changelog.id 204, 1 for delayed created)
       - slack: +1.0
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host; skipping live PG test.")

    tid = await _provision_test_tenant("ooo-426")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    jira_integ_id = uuid.uuid4()
    slack_integ_id = uuid.uuid4()

    async with get_tenant_db_context(tid) as db:
        db.add(
            Project(
                id=proj_id,
                tenant_id=tid,
                name="OOO 426 Pipeline Project",
                slug=f"ooo-426-{proj_id.hex[:6]}",
            )
        )
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=426001,
                full_name="acme/ooo-426-repo",
            )
        )
        db.add(
            Integration(
                id=jira_integ_id,
                tenant_id=tid,
                project_id=proj_id,
                provider="jira",
                external_account_id="jira-426",
                webhook_secret_hash="hash-jira-426",
                is_active=True,
            )
        )
        db.add(
            Integration(
                id=slack_integ_id,
                tenant_id=tid,
                project_id=proj_id,
                provider="slack",
                external_account_id="slack-426",
                webhook_secret_hash="hash-slack-426",
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

    # 1. GitHub: T+10m applied, T+2m rejected
    gh_ev1, gh_res1 = await _stage_and_process(
        provider="github",
        event_type="pull_request",
        payload={
            "action": "synchronize",
            "repository": {"full_name": "acme/ooo-426-repo"},
            "pull_request": {
                "number": 426,
                "title": "Authoritative T+10m PR Title",
                "state": "open",
                "head": {"ref": "feat/426", "sha": "b" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octocat"},
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
            "repository": {"full_name": "acme/ooo-426-repo"},
            "pull_request": {
                "number": 426,
                "title": "Stale T+2m PR Title",
                "state": "open",
                "head": {"ref": "feat/426", "sha": "a" * 40},
                "base": {"ref": "main"},
                "user": {"login": "octocat"},
                "created_at": "2026-11-19T10:00:00Z",
                "updated_at": "2026-11-19T10:02:00Z",
            },
        },
    )
    assert gh_res2 == "noop:stale_pr_version:426"
    assert _metric_sample("github") == before["github"] + 1.0

    # 2. Jira: changelog.id=205 applied, changelog.id=204 rejected, delayed created rejected
    jira_ev1, jira_res1 = await _stage_and_process(
        provider="jira",
        event_type="jira:issue_updated",
        integration_id=jira_integ_id,
        payload={
            "webhookEvent": "jira:issue_updated",
            "changelog": {"id": "205", "items": []},
            "issue": {
                "id": "42601",
                "key": "MANTA-426",
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
                "id": "42601",
                "key": "MANTA-426",
                "fields": {
                    "summary": "Stale In Progress at 204",
                    "status": {"name": "In Progress"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:02:00.000+0000",
                },
            },
        },
    )
    assert jira_res2 == "noop:stale_issue_version:MANTA-426"
    assert _metric_sample("jira") == before["jira"] + 1.0

    jira_ev3, jira_res3 = await _stage_and_process(
        provider="jira",
        event_type="jira:issue_created",
        integration_id=jira_integ_id,
        payload={
            "webhookEvent": "jira:issue_created",
            "issue": {
                "id": "42601",
                "key": "MANTA-426",
                "fields": {
                    "summary": "Delayed Initial Summary",
                    "status": {"name": "To Do"},
                    "issuetype": {"name": "Story"},
                    "updated": "2026-11-19T10:00:00.000+0000",
                },
            },
        },
    )
    assert jira_res3 == "noop:stale_issue_version:MANTA-426"
    assert _metric_sample("jira") == before["jira"] + 2.0

    # 3. Slack: 1700000010.000200 applied, 1700000005.000100 rejected
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
                "channel": "C426",
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
                "channel": "C426",
                "thread_ts": "1700000001.000001",
                "ts": "1700000005.000100",
                "event_ts": "1700000005.000100",
            },
        },
    )
    assert slack_res2 == "noop:stale_slack_event:C426:1700000001.000001"
    assert _metric_sample("slack") == before["slack"] + 1.0

    # Assert PostgreSQL stored state preservation
    async with get_tenant_db_context(tid) as db:
        pr_row = (
            await db.execute(
                select(PullRequest).where(
                    PullRequest.tenant_id == tid,
                    PullRequest.external_pr_number == 426,
                )
            )
        ).scalar_one()
        assert pr_row.title == "Authoritative T+10m PR Title"
        assert pr_row.head_sha == "b" * 40

        issue_row = (
            await db.execute(
                select(Issue).where(
                    Issue.tenant_id == tid,
                    Issue.external_key == "MANTA-426",
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
                    CommunicationEvent.channel_id == "C426",
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
