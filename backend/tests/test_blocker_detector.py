"""
TASK-307-0 & TASK-307-1 (SCRUM-542 / SCRUM-543 / STORY-307 — US-71):
Automated Verification Suite for Scheduled ARQ Blocker Detection
(>48h Stale PRs & 3-Incident / 60m Spikes), Deduplication State-Reset Rules,
Atomic Risk + OutboxEvent Dual-Commit, and SECURITY DEFINER Multi-Tenant RLS Sweep.

Specification Traceability:
- docs/blocker-detection-stale-pr-and-incident-spike-bdd-specs.md
- backend/tests/scenarios/test_us71_blocker_detection_stale_pr_and_incident_spike_scenarios.md
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
from unittest.mock import AsyncMock, patch
import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from app.blockers.service import (
    ACTIVE_RISK_STATUSES,
    RISK_DETECTED_EVENT_TYPE,
    RiskSeverity,
    RiskStatus,
    RiskSubjectType,
    RiskType,
    compute_pr_last_activity_at,
    detect_incident_spikes_in_session,
    detect_stale_prs_in_session,
    detect_tenant_blockers_in_session,
    is_pr_open,
    is_pr_stale_at,
    reset_pr_staleness_on_activity,
    should_reset_incident_spike_risk,
    upsert_blocker_risk_with_outbox,
)
from app.db.models.models import (
    Commit,
    Incident,
    OutboxEvent,
    Project,
    PullRequest,
    PullRequestReview,
    Repository,
    Risk,
    Tenant,
)
from app.db.session import async_engine, get_tenant_db_context
from app.git.normalization import (
    normalize_pull_request,
    normalize_pull_request_review,
    normalize_push,
)
from app.git.service import (
    process_pull_request,
    process_push,
)
from app.git.webhook_handler import handle_pull_request_review
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter
from workers.blocker_detector import (
    BLOCKER_CRON_LOCK_KEY,
    BLOCKER_CRON_MINUTES,
    BLOCKER_CRON_NAME,
    discover_active_tenants,
    run_blocker_detector_cron,
    run_blocker_detector_loop,
    sweep_all_active_tenants,
)
from workers.worker import WorkerSettings, dispatch_worker_job, run_blocker_detector_arq

# ---------------------------------------------------------------------------
# Live PostgreSQL Availability Probe (for CI & Docker Integration Tests)
# ---------------------------------------------------------------------------


def _is_live_db_available() -> bool:
    try:

        async def _ping() -> None:
            await async_engine.dispose()
            try:
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await async_engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = _is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL live MANTA database not reachable; skipping live PG RLS integration test",
)

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "b4d7e2f9c1a3_blocker_detector_indexes_and_active_tenants_rpc.py"
)


# ---------------------------------------------------------------------------
# Deterministic In-Memory Transactional Store & Fake AsyncSession
# ---------------------------------------------------------------------------


class _FakeScalarList:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeResult:
    def __init__(
        self,
        *,
        rows: Optional[list[Any]] = None,
        scalars_list: Optional[list[Any]] = None,
        scalar_one: Any = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows if rows is not None else []
        self._scalars = scalars_list if scalars_list is not None else []
        self._scalar_one = scalar_one
        self.rowcount = rowcount

    def scalars(self) -> _FakeScalarList:
        return _FakeScalarList(self._scalars)

    def scalar_one_or_none(self) -> Any:
        if self._scalar_one is not None:
            return self._scalar_one
        if self._scalars:
            return self._scalars[0]
        return None

    def all(self) -> list[Any]:
        return list(self._rows)

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _InMemoryTenantStore:
    """In-memory transactional mirror of tenant-scoped tables enforcing uq_risks_active_condition."""

    def __init__(self, tenant_id: uuid.UUID) -> None:
        self.tenant_id = tenant_id
        self.projects: dict[uuid.UUID, Project] = {}
        self.repositories: dict[uuid.UUID, Repository] = {}
        self.pull_requests: dict[uuid.UUID, PullRequest] = {}
        self.commits: dict[str, Commit] = {}
        self.reviews: dict[uuid.UUID, PullRequestReview] = {}
        self.incidents: dict[uuid.UUID, Incident] = {}
        self.risks: dict[uuid.UUID, Risk] = {}
        self.outbox_events: dict[uuid.UUID, OutboxEvent] = {}

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "projects": self.projects,
                "repositories": self.repositories,
                "pull_requests": self.pull_requests,
                "commits": self.commits,
                "reviews": self.reviews,
                "incidents": self.incidents,
                "risks": self.risks,
                "outbox_events": self.outbox_events,
            }
        )

    def restore(self, snap: dict[str, Any]) -> None:
        self.projects = snap["projects"]
        self.repositories = snap["repositories"]
        self.pull_requests = snap["pull_requests"]
        self.commits = snap["commits"]
        self.reviews = snap["reviews"]
        self.incidents = snap["incidents"]
        self.risks = snap["risks"]
        self.outbox_events = snap["outbox_events"]


class _InMemoryAsyncSession:
    """Statement-aware async session executing real blocker service SQL against _InMemoryTenantStore."""

    def __init__(self, store: _InMemoryTenantStore) -> None:
        self.store = store
        self.executed_sql: list[str] = []
        self.on_flush_hook: Optional[Any] = None

    def add(self, obj: Any) -> None:
        if isinstance(obj, OutboxEvent):
            self.store.outbox_events[obj.id] = obj
        elif isinstance(obj, PullRequestReview):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.store.reviews[obj.id] = obj
        elif isinstance(obj, Commit):
            self.store.commits[obj.sha] = obj
        elif isinstance(obj, PullRequest):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.store.pull_requests[obj.id] = obj
        elif isinstance(obj, Risk):
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.store.risks[obj.id] = obj

    async def flush(self) -> None:
        if self.on_flush_hook is not None:
            self.on_flush_hook()

    async def refresh(self, obj: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: Optional[dict[str, Any]] = None) -> _FakeResult:
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql_str = str(compiled)
        bound = dict(compiled.params)
        if params:
            bound.update(params)
        self.executed_sql.append(sql_str)
        sql_lower = sql_str.lower()

        # 1. INSERT INTO risks ... ON CONFLICT DO NOTHING RETURNING risks.id
        if sql_lower.startswith("insert into risks"):
            assert "on conflict" in sql_lower and "do nothing" in sql_lower
            assert "status in ('open', 'acknowledged')" in sql_lower
            key = (
                bound["tenant_id"],
                bound["project_id"],
                bound["type"],
                bound["subject_type"],
                bound["subject_id"],
            )
            for existing in self.store.risks.values():
                existing_key = (
                    existing.tenant_id,
                    existing.project_id,
                    existing.type,
                    existing.subject_type,
                    existing.subject_id,
                )
                if existing_key == key and existing.status in ACTIVE_RISK_STATUSES:
                    return _FakeResult(scalar_one=None)

            new_id = bound["id"]
            risk = Risk(
                id=new_id,
                tenant_id=bound["tenant_id"],
                project_id=bound["project_id"],
                type=bound["type"],
                severity=bound["severity"],
                status=bound["status"],
                subject_type=bound["subject_type"],
                subject_id=bound["subject_id"],
                created_at=bound["created_at"],
            )
            self.store.risks[new_id] = risk
            return _FakeResult(scalar_one=new_id)

        # 2. UPDATE pull_requests
        if sql_lower.startswith("update pull_requests"):
            # Verify `updated_at = pull_requests.updated_at` when flagging stale without new activity
            updated_count = 0
            new_is_stale = bound.get("is_stale")
            new_updated_at = bound.get("updated_at")
            new_entity_version = bound.get("entity_version")

            # Determine target PRs
            target_pr_id = bound.get("id_1")
            target_tenant_id = bound.get("tenant_id_1")
            incoming_ver_guard = bound.get("entity_version_1")
            is_terminal_cleanup = "merged_at is not null" in sql_lower

            updated_ids: list[uuid.UUID] = []
            for pr in self.store.pull_requests.values():
                if target_tenant_id is not None and pr.tenant_id != target_tenant_id:
                    continue
                if target_pr_id is not None and pr.id != target_pr_id:
                    continue
                if incoming_ver_guard is not None and not (pr.entity_version < incoming_ver_guard):
                    continue
                if is_terminal_cleanup:
                    if not pr.is_stale or is_pr_open(pr):
                        continue
                if new_is_stale is not None:
                    pr.is_stale = bool(new_is_stale)
                if new_updated_at is not None:
                    pr.updated_at = new_updated_at
                if new_entity_version is not None:
                    pr.entity_version = new_entity_version
                for field_name in ("title", "state", "head_branch", "base_branch", "head_sha"):
                    if field_name in bound:
                        setattr(pr, field_name, bound[field_name])
                if "merged_at" in bound:
                    pr.merged_at = bound["merged_at"]
                if "merge_commit_sha" in bound:
                    pr.merge_commit_sha = bound["merge_commit_sha"]
                if "closed_at" in bound:
                    pr.closed_at = bound["closed_at"]
                updated_count += 1
                updated_ids.append(pr.id)
            return _FakeResult(
                scalar_one=updated_ids[0] if updated_ids else None,
                rowcount=updated_count,
            )

        # 3. UPDATE risks SET status = 'RESOLVED'
        if sql_lower.startswith("update risks"):
            new_status = bound.get("status", RiskStatus.RESOLVED.value)
            target_tenant = bound.get("tenant_id_1")
            target_ids = bound.get("id_1")
            target_proj = bound.get("project_id_1")
            target_type = bound.get("type_1")
            target_subj_type = bound.get("subject_type_1")
            target_subj_id = bound.get("subject_id_1")
            status_in = bound.get("status_1")

            resolved = 0
            for risk in self.store.risks.values():
                if target_tenant is not None and risk.tenant_id != target_tenant:
                    continue
                if isinstance(target_ids, (list, tuple, set)) and risk.id not in target_ids:
                    continue
                if target_proj is not None and risk.project_id != target_proj:
                    continue
                if target_type is not None and risk.type != target_type:
                    continue
                if target_subj_type is not None and risk.subject_type != target_subj_type:
                    continue
                if target_subj_id is not None and risk.subject_id != target_subj_id:
                    continue
                if isinstance(status_in, (list, tuple, set)) and risk.status not in status_in:
                    continue
                risk.status = new_status
                resolved += 1
            return _FakeResult(rowcount=resolved)

        # 4. SELECT FROM projects
        if "from projects" in sql_lower:
            tid = bound.get("tenant_id_1")
            items = [
                p
                for p in self.store.projects.values()
                if (tid is None or p.tenant_id == tid) and p.is_active and p.deleted_at is None
            ]
            return _FakeResult(scalars_list=items)

        # 5. SELECT FROM repositories
        if "from repositories" in sql_lower:
            tid = bound.get("tenant_id_1")
            ext = bound.get("external_repo_id_1")
            full_name = bound.get("full_name_1")
            matches = [
                r
                for r in self.store.repositories.values()
                if (tid is None or r.tenant_id == tid)
                and (ext is None or r.external_repo_id == ext)
                and (full_name is None or r.full_name == full_name)
            ]
            return _FakeResult(scalars_list=matches)

        # 6. SELECT FROM pull_requests
        if "from pull_requests" in sql_lower:
            tid = bound.get("tenant_id_1")
            proj_ids = bound.get("project_id_1")
            repo_id = bound.get("repository_id_1")
            pr_num = bound.get("external_pr_number_1")
            head_branch = bound.get("head_branch_1")
            only_open = "lower(pull_requests.state) =" in sql_lower
            matches = []
            for pr in self.store.pull_requests.values():
                if tid is not None and pr.tenant_id != tid:
                    continue
                if isinstance(proj_ids, (list, tuple, set)) and pr.project_id not in proj_ids:
                    continue
                if repo_id is not None and pr.repository_id != repo_id:
                    continue
                if pr_num is not None and pr.external_pr_number != pr_num:
                    continue
                if head_branch is not None and pr.head_branch != head_branch:
                    continue
                if only_open and not is_pr_open(pr):
                    continue
                matches.append(pr)
            return _FakeResult(scalars_list=matches)

        # 7. SELECT FROM commits (grouped max or single lookup)
        if "from commits" in sql_lower:
            tid = bound.get("tenant_id_1")
            if "group by commits.pull_request_id" in sql_lower:
                pr_ids = set(bound.get("pull_request_id_1") or ())
                by_pr: dict[uuid.UUID, datetime] = {}
                for c in self.store.commits.values():
                    if tid is not None and c.tenant_id != tid:
                        continue
                    if c.pull_request_id not in pr_ids:
                        continue
                    ts = max(c.committed_at, c.authored_at)
                    if c.pull_request_id not in by_pr or ts > by_pr[c.pull_request_id]:
                        by_pr[c.pull_request_id] = ts
                return _FakeResult(rows=list(by_pr.items()))
            if "min(commits.authored_at)" in sql_lower:
                pr_id = bound.get("pull_request_id_1")
                ts_list = [
                    c.authored_at
                    for c in self.store.commits.values()
                    if (tid is None or c.tenant_id == tid) and c.pull_request_id == pr_id
                ]
                return _FakeResult(scalar_one=min(ts_list) if ts_list else None)
            sha = bound.get("sha_1")
            repo_id = bound.get("repository_id_1")
            matches = [
                c
                for c in self.store.commits.values()
                if (tid is None or c.tenant_id == tid)
                and (sha is None or c.sha == sha)
                and (repo_id is None or c.repository_id == repo_id)
            ]
            return _FakeResult(scalars_list=matches)

        # 8. SELECT FROM pull_request_reviews
        if "from pull_request_reviews" in sql_lower:
            tid = bound.get("tenant_id_1")
            if "group by pull_request_reviews.pull_request_id" in sql_lower:
                pr_ids = set(bound.get("pull_request_id_1") or ())
                by_pr: dict[uuid.UUID, datetime] = {}
                for rv in self.store.reviews.values():
                    if tid is not None and rv.tenant_id != tid:
                        continue
                    if rv.pull_request_id not in pr_ids:
                        continue
                    if (
                        rv.pull_request_id not in by_pr
                        or rv.submitted_at > by_pr[rv.pull_request_id]
                    ):
                        by_pr[rv.pull_request_id] = rv.submitted_at
                return _FakeResult(rows=list(by_pr.items()))
            pr_id = bound.get("pull_request_id_1")
            reviewer_p = bound.get("reviewer_pseudonym_id_1")
            sub_at = bound.get("submitted_at_1")
            matches = [
                rv
                for rv in self.store.reviews.values()
                if (tid is None or rv.tenant_id == tid)
                and (pr_id is None or rv.pull_request_id == pr_id)
                and (reviewer_p is None or rv.reviewer_pseudonym_id == reviewer_p)
                and (sub_at is None or rv.submitted_at == sub_at)
            ]
            return _FakeResult(scalars_list=matches)

        # 9. SELECT FROM incidents
        if "from incidents" in sql_lower:
            tid = bound.get("tenant_id_1")
            proj_ids = set(bound.get("project_id_1") or ())
            win_start = bound.get("started_at_1")
            win_end = bound.get("started_at_2")
            rows = [
                (inc.id, inc.project_id, inc.started_at)
                for inc in self.store.incidents.values()
                if (tid is None or inc.tenant_id == tid)
                and inc.project_id in proj_ids
                and (win_start is None or inc.started_at >= win_start)
                and (win_end is None or inc.started_at <= win_end)
            ]
            rows.sort(key=lambda r: (r[2], r[0]))
            return _FakeResult(rows=rows)

        # 10. SELECT FROM risks
        if "from risks" in sql_lower:
            tid = bound.get("tenant_id_1")
            proj_ids = bound.get("project_id_1")
            r_type = bound.get("type_1")
            subj_type = bound.get("subject_type_1")
            status_in = bound.get("status_1")
            matches = [
                r
                for r in self.store.risks.values()
                if (tid is None or r.tenant_id == tid)
                and (not isinstance(proj_ids, (list, tuple, set)) or r.project_id in proj_ids)
                and (r_type is None or r.type == r_type)
                and (subj_type is None or r.subject_type == subj_type)
                and (not isinstance(status_in, (list, tuple, set)) or r.status in status_in)
            ]
            return _FakeResult(scalars_list=matches)

        raise NotImplementedError(f"Unexpected SQL in _InMemoryAsyncSession: {sql_str}")


@asynccontextmanager
async def _transactional_memory_context(
    store: _InMemoryTenantStore,
    *,
    on_flush_hook: Optional[Any] = None,
) -> AsyncGenerator[_InMemoryAsyncSession, None]:
    """Emulates `get_tenant_db_context` commit/rollback atomicity over _InMemoryTenantStore."""
    snap = store.snapshot()
    session = _InMemoryAsyncSession(store)
    session.on_flush_hook = on_flush_hook
    try:
        yield session
    except Exception:
        store.restore(snap)
        raise


def _make_project(
    tenant_id: uuid.UUID,
    *,
    threshold_hours: int = 48,
    is_active: bool = True,
    deleted_at: Optional[datetime] = None,
    name: str = "Project Core",
) -> Project:
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    pid = uuid.uuid4()
    return Project(
        id=pid,
        tenant_id=tenant_id,
        name=name,
        slug=f"proj-{pid.hex[:8]}",
        pr_stale_threshold_hours=threshold_hours,
        is_active=is_active,
        deleted_at=deleted_at,
        created_at=now,
        updated_at=now,
    )


def _make_pr(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    repository_id: uuid.UUID,
    *,
    number: int = 101,
    state: str = "OPEN",
    created_at: datetime,
    updated_at: Optional[datetime] = None,
    is_stale: bool = False,
    head_branch: str = "feat/blocker",
    head_sha: str = "a" * 40,
    entity_version: int = 1000,
    merged_at: Optional[datetime] = None,
    closed_at: Optional[datetime] = None,
) -> PullRequest:
    return PullRequest(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repository_id,
        author_pseudonym_id="a" * 64,
        external_pr_number=number,
        title=f"PR #{number}",
        state=state,
        head_branch=head_branch,
        base_branch="main",
        head_sha=head_sha,
        created_in_git_at=created_at,
        updated_at=updated_at or created_at,
        merged_at=merged_at,
        closed_at=closed_at,
        entity_version=entity_version,
        is_stale=is_stale,
        review_cycles_count=0,
    )


# ==============================================================================
# GROUP 1: STALE PULL REQUEST DETECTION & THRESHOLD BOUNDARIES (TC-BLOCKER-001..005)
# ==============================================================================


class TestStalePullRequestDetection:
    """Deterministic unit & behavioral verification for STALE_PR detection and deduplication."""

    @pytest.mark.asyncio
    async def test_stale_pr_detection_atomic_risk_and_outbox_preserves_updated_at(self) -> None:
        """TC-BLOCKER-001: PR idle >48h -> is_stale=True (updated_at preserved) + 1 Risk + 1 OutboxEvent."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        original_updated = now - timedelta(hours=49)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        repo_id = uuid.uuid4()
        pr = _make_pr(tid, proj.id, repo_id, number=101, created_at=original_updated)
        store.pull_requests[pr.id] = pr

        async with _transactional_memory_context(store) as session:
            stats = await detect_stale_prs_in_session(session, tenant_id=tid, now=now)

        assert stats.projects_evaluated == 1
        assert stats.prs_evaluated == 1
        assert stats.prs_flagged_stale == 1
        assert stats.risks_created == 1
        assert stats.outbox_events_staged == 1
        assert stats.duplicates_suppressed == 0

        # Invariant: is_stale=True AND updated_at preserved (NOT bumped to `now`)
        assert store.pull_requests[pr.id].is_stale is True
        assert store.pull_requests[pr.id].updated_at == original_updated

        # Exactly 1 Risk + 1 OutboxEvent committed atomically
        assert len(store.risks) == 1
        risk = next(iter(store.risks.values()))
        assert risk.tenant_id == tid
        assert risk.project_id == proj.id
        assert risk.type == RiskType.STALE_PR.value
        assert risk.severity == RiskSeverity.HIGH.value
        assert risk.status == RiskStatus.OPEN.value
        assert risk.subject_type == RiskSubjectType.PULL_REQUEST.value
        assert risk.subject_id == str(pr.id)

        assert len(store.outbox_events) == 1
        outbox = next(iter(store.outbox_events.values()))
        assert outbox.tenant_id == tid
        assert outbox.event_type == RISK_DETECTED_EVENT_TYPE
        assert outbox.aggregate_type == "risk"
        assert outbox.aggregate_id == str(risk.id)
        assert outbox.published_at is None
        assert outbox.payload["schema_version"] == "1.0"
        assert outbox.payload["data"]["risk_type"] == "STALE_PR"
        assert outbox.payload["data"]["threshold_hours"] == 48
        assert outbox.payload["data"]["idle_hours"] == 49.0
        assert outbox.payload["data"]["author_pseudonym_id"] == "a" * 64

    @pytest.mark.parametrize(
        ("threshold_hours", "idle_seconds", "expected_stale"),
        [
            (48, 48 * 3600 - 1, False),
            (48, 48 * 3600, False),  # Exact boundary: strictly greater than (`>`)
            (48, 48 * 3600 + 1, True),
            (24, 24 * 3600, False),
            (24, 30 * 3600, True),
            (72, 50 * 3600, False),
            (72, 73 * 3600, True),
        ],
    )
    @pytest.mark.asyncio
    async def test_stale_pr_threshold_boundary_and_custom_project_hours(
        self,
        threshold_hours: int,
        idle_seconds: int,
        expected_stale: bool,
    ) -> None:
        """TC-BLOCKER-002: Enforce strict `>` boundary and custom Project.pr_stale_threshold_hours."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        last_act = now - timedelta(seconds=idle_seconds)

        proj = _make_project(tid, threshold_hours=threshold_hours)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), state="open", created_at=last_act)
        store.pull_requests[pr.id] = pr

        assert is_pr_stale_at(last_act, threshold_hours=threshold_hours, now=now) is expected_stale

        async with _transactional_memory_context(store) as session:
            stats = await detect_stale_prs_in_session(session, tenant_id=tid, now=now)

        assert store.pull_requests[pr.id].is_stale is expected_stale
        expected_count = 1 if expected_stale else 0
        assert stats.risks_created == expected_count
        assert stats.outbox_events_staged == expected_count
        assert len(store.risks) == expected_count
        assert len(store.outbox_events) == expected_count

    @pytest.mark.asyncio
    async def test_recent_commit_or_review_prevents_stale_flagging(self) -> None:
        """TC-BLOCKER-003: Linked Commit or PullRequestReview within threshold keeps old PR fresh."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        repo_id = uuid.uuid4()

        # PR 1: created 72h ago, recent Commit 10h ago
        pr_commit = _make_pr(
            tid, proj.id, repo_id, number=201, created_at=now - timedelta(hours=72)
        )
        store.pull_requests[pr_commit.id] = pr_commit
        store.commits["b" * 40] = Commit(
            sha="b" * 40,
            tenant_id=tid,
            repository_id=repo_id,
            author_pseudonym_id="a" * 64,
            committer_pseudonym_id="a" * 64,
            authored_at=now - timedelta(hours=10),
            committed_at=now - timedelta(hours=10),
            pull_request_id=pr_commit.id,
        )

        # PR 2: created 72h ago, recent PullRequestReview 5h ago
        pr_review = _make_pr(
            tid, proj.id, repo_id, number=202, created_at=now - timedelta(hours=72)
        )
        store.pull_requests[pr_review.id] = pr_review
        rv_id = uuid.uuid4()
        store.reviews[rv_id] = PullRequestReview(
            id=rv_id,
            tenant_id=tid,
            pull_request_id=pr_review.id,
            reviewer_pseudonym_id="c" * 64,
            state="COMMENTED",
            review_cycle_index=1,
            submitted_at=now - timedelta(hours=5),
        )

        assert compute_pr_last_activity_at(
            pr_commit, latest_commit_at=now - timedelta(hours=10)
        ) == now - timedelta(hours=10)

        async with _transactional_memory_context(store) as session:
            stats = await detect_stale_prs_in_session(session, tenant_id=tid, now=now)

        assert stats.prs_evaluated == 2
        assert stats.risks_created == 0
        assert stats.outbox_events_staged == 0
        assert store.pull_requests[pr_commit.id].is_stale is False
        assert store.pull_requests[pr_review.id].is_stale is False

    @pytest.mark.asyncio
    async def test_stale_pr_duplicate_suppression_open_and_acknowledged(self) -> None:
        """TC-BLOCKER-004: Repeated 15m sweeps produce 0 duplicate Risks/OutboxEvents for OPEN & ACK."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), created_at=t0 - timedelta(hours=50))
        store.pull_requests[pr.id] = pr

        # Sweep 1 at T0 -> creates 1 Risk + 1 OutboxEvent
        async with _transactional_memory_context(store) as session:
            s1 = await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        assert s1.risks_created == 1 and s1.outbox_events_staged == 1

        # Sweep 2 & 3 at T0+15m and T0+30m while Risk is OPEN -> 0 new rows
        for offset_min in (15, 30):
            async with _transactional_memory_context(store) as session:
                s = await detect_stale_prs_in_session(
                    session, tenant_id=tid, now=t0 + timedelta(minutes=offset_min)
                )
            assert s.risks_created == 0
            assert s.outbox_events_staged == 0
            assert s.duplicates_suppressed == 1

        # Manager acknowledges Risk -> status = ACKNOWLEDGED
        risk = next(iter(store.risks.values()))
        risk.status = RiskStatus.ACKNOWLEDGED.value
        risk.acknowledged_by = uuid.uuid4()

        # Sweep 4 at T0+45m while Risk is ACKNOWLEDGED -> still suppressed (0 alert fatigue)
        async with _transactional_memory_context(store) as session:
            s4 = await detect_stale_prs_in_session(
                session, tenant_id=tid, now=t0 + timedelta(minutes=45)
            )
        assert s4.risks_created == 0
        assert s4.outbox_events_staged == 0
        assert s4.duplicates_suppressed == 1
        assert len(store.risks) == 1
        assert len(store.outbox_events) == 1
        assert next(iter(store.risks.values())).status == RiskStatus.ACKNOWLEDGED.value


# ==============================================================================
# GROUP 2: STALE PR STATE-RESET & RE-ARMING RULES (TC-BLOCKER-006..010)
# ==============================================================================


class TestStalePrStateResetRules:
    """Verification of clean is_stale=false reset on commit/review/close and re-arming."""

    @pytest.mark.asyncio
    async def test_push_and_synchronize_reset_is_stale_and_resolve_risk(self) -> None:
        """TC-BLOCKER-006: GitHub push & pull_request synchronize reset is_stale=False and resolve Risk."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        repo = Repository(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=proj.id,
            external_repo_id=7771,
            full_name="acme/core",
        )
        store.repositories[repo.id] = repo
        pr = _make_pr(
            tid,
            proj.id,
            repo.id,
            number=101,
            head_branch="feat/reset",
            created_at=t0 - timedelta(hours=60),
            entity_version=1_700_000_000_000,
        )
        store.pull_requests[pr.id] = pr

        # Flag stale first
        async with _transactional_memory_context(store) as session:
            await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        assert store.pull_requests[pr.id].is_stale is True
        assert next(iter(store.risks.values())).status == RiskStatus.OPEN.value

        # 1. Reset via GitHub `push` webhook on PR's head_branch
        push_payload = {
            "ref": "refs/heads/feat/reset",
            "repository": {"id": 7771, "full_name": "acme/core"},
            "commits": [
                {
                    "id": "d" * 40,
                    "timestamp": (t0 + timedelta(hours=1)).isoformat(),
                    "author": {"username": "alice", "name": "Alice", "email": "alice@acme.io"},
                }
            ],
        }
        with patch(
            "app.git.service.resolve_or_create_contributor_pseudonym",
            new=AsyncMock(return_value="f" * 64),
        ):
            async with _transactional_memory_context(store) as session:
                res = await process_push(session, tenant_id=tid, n=normalize_push(push_payload))
        assert res == "persisted_push:1"
        assert store.pull_requests[pr.id].is_stale is False
        assert store.pull_requests[pr.id].updated_at == t0 + timedelta(hours=1)
        assert next(iter(store.risks.values())).status == RiskStatus.RESOLVED.value
        assert len(store.outbox_events) == 1  # No noisy outbox event on reset

        # 2. Flag stale again at T0 + 55h, then reset via `pull_request` synchronize
        t_stale_2 = t0 + timedelta(hours=55)
        async with _transactional_memory_context(store) as session:
            s_rearm = await detect_stale_prs_in_session(session, tenant_id=tid, now=t_stale_2)
        assert s_rearm.risks_created == 1
        assert store.pull_requests[pr.id].is_stale is True

        sync_ts = t_stale_2 + timedelta(hours=1)
        sync_payload = {
            "action": "synchronize",
            "repository": {"id": 7771, "full_name": "acme/core"},
            "pull_request": {
                "number": 101,
                "title": "PR #101 updated",
                "state": "open",
                "head": {"ref": "feat/reset", "sha": "e" * 40},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
                "created_at": (t0 - timedelta(hours=60)).isoformat(),
                "updated_at": sync_ts.isoformat(),
            },
        }
        async with _transactional_memory_context(store) as session:
            res_sync = await process_pull_request(
                session, tenant_id=tid, n=normalize_pull_request(sync_payload)
            )
        assert res_sync == f"persisted_pr:{pr.id}"
        assert store.pull_requests[pr.id].is_stale is False
        assert all(r.status == RiskStatus.RESOLVED.value for r in store.risks.values())

    @pytest.mark.asyncio
    async def test_pull_request_review_webhook_resets_is_stale_without_pii(self) -> None:
        """TC-BLOCKER-007: GitHub pull_request_review resets is_stale=False, resolves Risk, stores 0 PII."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        repo = Repository(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=proj.id,
            external_repo_id=8882,
            full_name="acme/payments",
        )
        store.repositories[repo.id] = repo
        pr = _make_pr(tid, proj.id, repo.id, number=42, created_at=t0 - timedelta(hours=55))
        store.pull_requests[pr.id] = pr

        # Flag stale & acknowledge
        async with _transactional_memory_context(store) as session:
            await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        next(iter(store.risks.values())).status = RiskStatus.ACKNOWLEDGED.value
        assert store.pull_requests[pr.id].is_stale is True

        # Process pull_request_review submitted webhook
        review_ts = t0 + timedelta(minutes=30)
        review_event = type(
            "FakeWebhookEvent",
            (),
            {
                "integration_id": None,
                "payload": {
                    "action": "submitted",
                    "repository": {"id": 8882, "full_name": "acme/payments"},
                    "pull_request": {"number": 42, "state": "open"},
                    "review": {
                        "state": "changes_requested",
                        "submitted_at": review_ts.isoformat(),
                        "user": {
                            "login": "bob-reviewer",
                            "name": "Bob Reviewer",
                            "email": "bob@secret.corp",
                        },
                    },
                },
            },
        )()

        pseudonym_hex = "9" * 64
        with patch(
            "app.git.service.resolve_or_create_contributor_pseudonym",
            new=AsyncMock(return_value=pseudonym_hex),
        ):
            async with _transactional_memory_context(store) as session:
                result = await handle_pull_request_review(session, review_event, tid)

        assert result.startswith("persisted_pr_review:")
        assert len(store.reviews) == 1
        rv = next(iter(store.reviews.values()))
        assert rv.state == "CHANGES_REQUESTED"
        assert rv.reviewer_pseudonym_id == pseudonym_hex
        assert "bob" not in rv.reviewer_pseudonym_id.lower()
        assert rv.submitted_at == review_ts

        # PR is_stale reset cleanly and ACKNOWLEDGED Risk resolved
        assert store.pull_requests[pr.id].is_stale is False
        assert store.pull_requests[pr.id].first_review_at == review_ts
        assert store.pull_requests[pr.id].review_cycles_count == 1
        assert store.pull_requests[pr.id].updated_at == review_ts
        assert next(iter(store.risks.values())).status == RiskStatus.RESOLVED.value

        # Normalization rejects unhandled review actions cleanly
        noop_rev = normalize_pull_request_review(
            {"action": "dismissed", "pull_request": {"number": 42}}
        )
        assert noop_rev.kind == "noop"

    @pytest.mark.asyncio
    async def test_out_of_order_stale_pr_webhook_does_not_reset_is_stale(self) -> None:
        """TC-BLOCKER-008: Delayed out-of-order synchronize webhook rejected without clearing is_stale."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        repo = Repository(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=proj.id,
            external_repo_id=9993,
            full_name="acme/ooo",
        )
        store.repositories[repo.id] = repo
        pr = _make_pr(
            tid,
            proj.id,
            repo.id,
            number=55,
            created_at=t0 - timedelta(hours=60),
            entity_version=2_000_000_000_000,  # Far newer than delayed webhook
        )
        store.pull_requests[pr.id] = pr

        async with _transactional_memory_context(store) as session:
            await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        assert store.pull_requests[pr.id].is_stale is True

        # Delayed synchronize with older updated_at (epoch ms < 2_000_000_000_000)
        delayed_sync = normalize_pull_request(
            {
                "action": "synchronize",
                "repository": {"id": 9993, "full_name": "acme/ooo"},
                "pull_request": {
                    "number": 55,
                    "title": "Old sync",
                    "state": "open",
                    "head": {"ref": "feat/blocker", "sha": "b" * 40},
                    "base": {"ref": "main"},
                    "user": {"login": "alice"},
                    "updated_at": "2026-11-23T00:00:00Z",
                },
            }
        )
        async with _transactional_memory_context(store) as session:
            res = await process_pull_request(session, tenant_id=tid, n=delayed_sync)

        assert res == "noop:stale_pr_version:55"
        assert store.pull_requests[pr.id].is_stale is True
        assert next(iter(store.risks.values())).status == RiskStatus.OPEN.value

    @pytest.mark.asyncio
    async def test_stale_pr_rearms_cleanly_after_reset_and_new_idle_period(self) -> None:
        """TC-BLOCKER-009: After reset + another >threshold idle window, emits Risk #2 + OutboxEvent #2."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), created_at=t0 - timedelta(hours=50))
        store.pull_requests[pr.id] = pr

        # Cycle 1: Flag stale
        async with _transactional_memory_context(store) as session:
            s1 = await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        assert s1.risks_created == 1
        risk_1_id = next(iter(store.risks.keys()))

        # Activity reset at T0 + 2h
        reset_ts = t0 + timedelta(hours=2)
        async with _transactional_memory_context(store) as session:
            was_stale, resolved = await reset_pr_staleness_on_activity(
                session, tenant_id=tid, pr=store.pull_requests[pr.id], activity_at=reset_ts
            )
        assert was_stale is True and resolved == 1
        assert store.risks[risk_1_id].status == RiskStatus.RESOLVED.value
        assert store.pull_requests[pr.id].is_stale is False

        # Sweep at T0 + 49h (47h since reset_ts -> still fresh)
        async with _transactional_memory_context(store) as session:
            s_mid = await detect_stale_prs_in_session(
                session, tenant_id=tid, now=t0 + timedelta(hours=49)
            )
        assert s_mid.risks_created == 0
        assert store.pull_requests[pr.id].is_stale is False

        # Cycle 2: Sweep at T0 + 52h (50h since reset_ts > 48h -> re-arms!)
        async with _transactional_memory_context(store) as session:
            s2 = await detect_stale_prs_in_session(
                session, tenant_id=tid, now=t0 + timedelta(hours=52)
            )
        assert s2.risks_created == 1
        assert s2.outbox_events_staged == 1
        assert store.pull_requests[pr.id].is_stale is True
        assert len(store.risks) == 2
        assert len(store.outbox_events) == 2
        assert store.risks[risk_1_id].status == RiskStatus.RESOLVED.value
        open_risks = [r for r in store.risks.values() if r.status == RiskStatus.OPEN.value]
        assert len(open_risks) == 1
        assert open_risks[0].id != risk_1_id

    @pytest.mark.asyncio
    async def test_closed_and_merged_prs_clear_stale_flag_and_resolve_risk(self) -> None:
        """TC-BLOCKER-010: Closed/merged PRs clear is_stale, resolve Risk, and are skipped in sweeps."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), created_at=t0 - timedelta(hours=60))
        store.pull_requests[pr.id] = pr

        async with _transactional_memory_context(store) as session:
            await detect_stale_prs_in_session(session, tenant_id=tid, now=t0)
        assert store.pull_requests[pr.id].is_stale is True

        # PR transitions to MERGED externally; sweep self-healing cleans up stale flag & Risk
        store.pull_requests[pr.id].state = "MERGED"
        store.pull_requests[pr.id].merged_at = t0 + timedelta(minutes=5)
        store.pull_requests[pr.id].closed_at = t0 + timedelta(minutes=5)

        async with _transactional_memory_context(store) as session:
            s_clean = await detect_stale_prs_in_session(
                session, tenant_id=tid, now=t0 + timedelta(hours=72)
            )
        assert s_clean.prs_evaluated == 0
        assert s_clean.prs_reset == 1
        assert s_clean.risks_resolved == 1
        assert s_clean.risks_created == 0
        assert store.pull_requests[pr.id].is_stale is False
        assert next(iter(store.risks.values())).status == RiskStatus.RESOLVED.value


# ==============================================================================
# GROUP 3: 3-INCIDENT / 60-MINUTE SPIKE DETECTION & RESET (TC-BLOCKER-011..014)
# ==============================================================================


class TestIncidentSpikeDetection:
    """Verification of 3-incident/60m spike detection, duplicate suppression & anti-flapping reset."""

    @pytest.mark.asyncio
    async def test_incident_spike_three_in_60m_commits_risk_and_outbox(self) -> None:
        """TC-BLOCKER-011: >= 3 incidents in rolling 60m commits 1 CRITICAL Risk + 1 OutboxEvent."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid)
        store.projects[proj.id] = proj

        inc_ids = []
        for minutes_ago in (45, 20, 5):
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj.id,
                started_at=now - timedelta(minutes=minutes_ago),
                resolved_at=(now - timedelta(minutes=minutes_ago - 2))
                if minutes_ago == 45
                else None,
                severity="SEV-1",
                created_at=now - timedelta(minutes=minutes_ago),
            )
            store.incidents[inc.id] = inc
            inc_ids.append(str(inc.id))

        async with _transactional_memory_context(store) as session:
            stats = await detect_incident_spikes_in_session(session, tenant_id=tid, now=now)

        assert stats.projects_evaluated == 1
        assert stats.spikes_detected == 1
        assert stats.risks_created == 1
        assert stats.outbox_events_staged == 1
        assert stats.duplicates_suppressed == 0

        risk = next(iter(store.risks.values()))
        assert risk.type == RiskType.INCIDENT_SPIKE.value
        assert risk.severity == RiskSeverity.CRITICAL.value
        assert risk.status == RiskStatus.OPEN.value
        assert risk.subject_type == RiskSubjectType.PROJECT.value
        assert risk.subject_id == str(proj.id)

        outbox = next(iter(store.outbox_events.values()))
        assert outbox.event_type == RISK_DETECTED_EVENT_TYPE
        assert outbox.aggregate_type == "risk"
        assert outbox.aggregate_id == str(risk.id)
        assert outbox.published_at is None
        assert outbox.payload["data"]["risk_type"] == "INCIDENT_SPIKE"
        assert outbox.payload["data"]["incident_count"] == 3
        assert outbox.payload["data"]["window_minutes"] == 60
        assert outbox.payload["data"]["incident_ids"] == inc_ids

    @pytest.mark.asyncio
    async def test_incident_spike_subthreshold_expired_and_cross_project_ignored(self) -> None:
        """TC-BLOCKER-012: 2 fresh + 1 expired (60m01s) and 2+2 cross-project split emit 0 alerts."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj_a = _make_project(tid, name="Core")
        proj_b = _make_project(tid, name="Billing")
        store.projects[proj_a.id] = proj_a
        store.projects[proj_b.id] = proj_b

        # Project A: 2 in window + 1 expired by 1 second (60m01s ago)
        for delta in (
            timedelta(minutes=60, seconds=1),
            timedelta(minutes=30),
            timedelta(minutes=10),
        ):
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj_a.id,
                started_at=now - delta,
                severity="SEV-1",
                created_at=now - delta,
            )
            store.incidents[inc.id] = inc

        # Project B: 2 in window
        for delta in (timedelta(minutes=15), timedelta(minutes=5)):
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj_b.id,
                started_at=now - delta,
                severity="SEV-2",
                created_at=now - delta,
            )
            store.incidents[inc.id] = inc

        async with _transactional_memory_context(store) as session:
            stats = await detect_incident_spikes_in_session(session, tenant_id=tid, now=now)

        assert stats.projects_evaluated == 2
        assert stats.spikes_detected == 0
        assert stats.risks_created == 0
        assert stats.outbox_events_staged == 0
        assert len(store.risks) == 0
        assert len(store.outbox_events) == 0

    @pytest.mark.asyncio
    async def test_incident_spike_suppresses_duplicates_during_storm_and_ack(self) -> None:
        """TC-BLOCKER-013: Ongoing storm (4th/5th incident) & ACKNOWLEDGED status produce 0 duplicates."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        t0 = datetime(2026, 11, 25, 10, 15, 0, tzinfo=timezone.utc)

        proj = _make_project(tid)
        store.projects[proj.id] = proj

        for offset in (15, 10, 5):
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj.id,
                started_at=t0 - timedelta(minutes=offset),
                severity="SEV-1",
                created_at=t0 - timedelta(minutes=offset),
            )
            store.incidents[inc.id] = inc

        # Initial sweep at 10:15 -> 1 Risk + 1 OutboxEvent
        async with _transactional_memory_context(store) as session:
            s1 = await detect_incident_spikes_in_session(session, tenant_id=tid, now=t0)
        assert s1.risks_created == 1 and s1.outbox_events_staged == 1

        # 4th incident arrives at 10:25; sweeps at 10:30 & 10:45 -> 0 new Risks/OutboxEvents
        inc4 = Incident(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=proj.id,
            started_at=t0 + timedelta(minutes=10),
            severity="SEV-1",
            created_at=t0 + timedelta(minutes=10),
        )
        store.incidents[inc4.id] = inc4

        async with _transactional_memory_context(store) as session:
            s2 = await detect_incident_spikes_in_session(
                session, tenant_id=tid, now=t0 + timedelta(minutes=15)
            )
        assert s2.risks_created == 0 and s2.duplicates_suppressed == 1

        # Operator acknowledges Risk
        next(iter(store.risks.values())).status = RiskStatus.ACKNOWLEDGED.value

        async with _transactional_memory_context(store) as session:
            s3 = await detect_incident_spikes_in_session(
                session, tenant_id=tid, now=t0 + timedelta(minutes=30)
            )
        assert s3.risks_created == 0 and s3.duplicates_suppressed == 1
        assert len(store.risks) == 1
        assert len(store.outbox_events) == 1

    @pytest.mark.asyncio
    async def test_incident_spike_anti_flapping_cooldown_reset_and_rearm(self) -> None:
        """TC-BLOCKER-014: Cooldown hold prevents boundary flapping; quiet/cooldown resolves & re-arms."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        base = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid)
        store.projects[proj.id] = proj

        # 3 incidents at 10:00, 10:05, 10:10
        for minute in (0, 5, 10):
            ts = base + timedelta(minutes=minute)
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj.id,
                started_at=ts,
                severity="SEV-1",
                created_at=ts,
            )
            store.incidents[inc.id] = inc

        # Sweep at 10:15 -> fires Risk #1
        t_fire = base + timedelta(minutes=15)
        async with _transactional_memory_context(store) as session:
            s1 = await detect_incident_spikes_in_session(session, tenant_id=tid, now=t_fire)
        assert s1.risks_created == 1
        risk_1_id = next(iter(store.risks.keys()))

        # Sweep at 11:02: 10:00 aged out -> count=2, age=47m < 60m -> HELD (anti-flapping!)
        t_dip = base + timedelta(minutes=62)
        assert (
            should_reset_incident_spike_risk(incident_count=2, risk_created_at=t_fire, now=t_dip)
            is False
        )
        async with _transactional_memory_context(store) as session:
            s_dip = await detect_incident_spikes_in_session(session, tenant_id=tid, now=t_dip)
        assert s_dip.cooldown_held == 1
        assert s_dip.risks_resolved == 0
        assert store.risks[risk_1_id].status == RiskStatus.OPEN.value

        # Sweep at 11:15: all 3 aged out (count=0, age=60m) -> RESOLVED cleanly!
        t_quiet = base + timedelta(minutes=75)
        async with _transactional_memory_context(store) as session:
            s_quiet = await detect_incident_spikes_in_session(session, tenant_id=tid, now=t_quiet)
        assert s_quiet.risks_resolved == 1
        assert store.risks[risk_1_id].status == RiskStatus.RESOLVED.value

        # 3 brand-new incidents at 11:20, 11:25, 11:30 -> Sweep at 11:30 emits Risk #2 + Outbox #2
        for minute in (80, 85, 90):
            ts = base + timedelta(minutes=minute)
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=proj.id,
                started_at=ts,
                severity="SEV-1",
                created_at=ts,
            )
            store.incidents[inc.id] = inc

        async with _transactional_memory_context(store) as session:
            s2 = await detect_incident_spikes_in_session(
                session, tenant_id=tid, now=base + timedelta(minutes=90)
            )
        assert s2.risks_created == 1
        assert s2.outbox_events_staged == 1
        assert len(store.risks) == 2
        assert len(store.outbox_events) == 2
        assert store.risks[risk_1_id].status == RiskStatus.RESOLVED.value


# ==============================================================================
# GROUP 4: ATOMIC ROLLBACK, MULTI-TENANT DISCOVERY, FAULT ISOLATION & ARQ CRON
# ==============================================================================


class TestAtomicityDiscoveryAndArqScheduling:
    """Verification of rollback atomicity, SECURITY DEFINER pagination, fault isolation & ARQ cron."""

    @pytest.mark.asyncio
    async def test_atomic_rollback_prevents_partial_risk_or_outbox_writes(self) -> None:
        """TC-BLOCKER-015: Unhandled error prior to commit rolls back is_stale, Risk & OutboxEvent."""
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), created_at=now - timedelta(hours=60))
        store.pull_requests[pr.id] = pr

        def _crash_on_flush() -> None:
            raise RuntimeError("Simulated PostgreSQL commit failure")

        with pytest.raises(RuntimeError, match="Simulated PostgreSQL commit failure"):
            async with _transactional_memory_context(
                store, on_flush_hook=_crash_on_flush
            ) as session:
                await detect_tenant_blockers_in_session(session, tenant_id=tid, now=now)

        # Zero partial writes across all tables
        assert store.pull_requests[pr.id].is_stale is False
        assert len(store.risks) == 0
        assert len(store.outbox_events) == 0

    @pytest.mark.asyncio
    async def test_discover_active_tenants_pagination(self) -> None:
        """TC-BLOCKER-016 (unit): Keyset pagination calls list_active_tenants until exhausted."""
        t1 = uuid.UUID("00000000-0000-0000-0000-000000000001")
        t2 = uuid.UUID("00000000-0000-0000-0000-000000000002")
        t3 = uuid.UUID("00000000-0000-0000-0000-000000000003")
        pages = [[(t1,), (t2,)], [(t3,)]]
        seen_params: list[dict[str, Any]] = []

        class _DiscoverySession:
            @asynccontextmanager
            async def begin(self) -> AsyncGenerator[None, None]:
                yield

            async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
                assert "list_active_tenants" in str(stmt)
                seen_params.append(dict(params))
                idx = len(seen_params) - 1
                return _FakeResult(rows=pages[idx] if idx < len(pages) else [])

        @asynccontextmanager
        async def _factory() -> AsyncGenerator[_DiscoverySession, None]:
            yield _DiscoverySession()

        discovered = await discover_active_tenants(batch_size=2, session_factory=_factory)
        assert discovered == [t1, t2, t3]
        assert seen_params == [
            {"limit": 2, "after_id": None},
            {"limit": 2, "after_id": t2},
        ]

    @pytest.mark.asyncio
    async def test_sweep_all_active_tenants_isolates_single_tenant_failure(self) -> None:
        """TC-BLOCKER-017: Exception in Tenant A is isolated so Tenant B commits cleanly."""
        tid_a = uuid.uuid4()
        tid_b = uuid.uuid4()
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

        store_b = _InMemoryTenantStore(tid_b)
        proj_b = _make_project(tid_b, name="Beta Proj")
        store_b.projects[proj_b.id] = proj_b
        for mins in (30, 15, 5):
            inc = Incident(
                id=uuid.uuid4(),
                tenant_id=tid_b,
                project_id=proj_b.id,
                started_at=now - timedelta(minutes=mins),
                severity="SEV-1",
                created_at=now - timedelta(minutes=mins),
            )
            store_b.incidents[inc.id] = inc

        @asynccontextmanager
        async def _tenant_factory(tid: uuid.UUID) -> AsyncGenerator[_InMemoryAsyncSession, None]:
            if tid == tid_a:
                raise ConnectionError("Tenant A transient DB failure")
            async with _transactional_memory_context(store_b) as sess:
                yield sess

        res = await sweep_all_active_tenants(
            now=now,
            tenant_ids=[tid_a, tid_b],
            tenant_session_factory=_tenant_factory,
        )

        assert res.tenants_discovered == 2
        assert res.tenants_swept == 1
        assert res.tenant_errors == 1
        assert res.incident_spike_risks_created == 1
        assert res.outbox_events_staged == 1
        assert len(store_b.risks) == 1
        assert len(store_b.outbox_events) == 1

    @pytest.mark.asyncio
    async def test_arq_worker_settings_registers_15m_blocker_cron(self, tmp_path: Path) -> None:
        """TC-BLOCKER-018: WorkerSettings registers 15m ARQ cron job, dispatch route & daemon loop."""
        cron_jobs = getattr(WorkerSettings, "cron_jobs", [])
        matching = [job for job in cron_jobs if getattr(job, "name", None) == BLOCKER_CRON_NAME]
        assert len(matching) == 1
        cron_job = matching[0]
        assert cron_job.coroutine is run_blocker_detector_arq
        assert set(cron_job.minute) == set(BLOCKER_CRON_MINUTES)
        assert cron_job.second == 0
        assert cron_job.unique is True
        assert run_blocker_detector_arq in WorkerSettings.functions

        # Verify ARQ cron entrypoint & worker job dispatcher execute cleanly
        tid = uuid.uuid4()
        store = _InMemoryTenantStore(tid)
        now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        proj = _make_project(tid, threshold_hours=48)
        store.projects[proj.id] = proj
        pr = _make_pr(tid, proj.id, uuid.uuid4(), created_at=now - timedelta(hours=50))
        store.pull_requests[pr.id] = pr

        @asynccontextmanager
        async def _factory(target_tid: uuid.UUID) -> AsyncGenerator[_InMemoryAsyncSession, None]:
            assert target_tid == tid
            async with _transactional_memory_context(store) as sess:
                yield sess

        summary = await run_blocker_detector_cron(
            {"now": now, "tenant_ids": [tid], "tenant_session_factory": _factory}
        )
        assert summary["stale_pr_risks_created"] == 1
        assert summary["outbox_events_staged"] == 1

        # Verify dispatch_worker_job handles job_type="blocker.detect"
        with patch(
            "workers.blocker_detector.run_blocker_detector_cron",
            new=AsyncMock(return_value={"tenants_swept": 1}),
        ) as mock_cron:
            dispatched = await dispatch_worker_job({"job_type": "blocker.detect", "payload": {}})
        assert dispatched == {"tenants_swept": 1}
        mock_cron.assert_awaited_once()

        # Verify background daemon loop touches heartbeat & respects Redis NX lock
        class _FakeLockRedis:
            def __init__(self) -> None:
                self.values: dict[str, str] = {}

            async def set(
                self, key: str, value: str, *, nx: bool = False, ex: Optional[int] = None
            ) -> bool:
                if nx and key in self.values:
                    return False
                self.values[key] = value
                return True

            async def get(self, key: str) -> Optional[str]:
                return self.values.get(key)

        redis_mock = _FakeLockRedis()
        stop_event = asyncio.Event()
        hb_file = tmp_path / "blocker.heartbeat"
        discovery_calls = 0

        @asynccontextmanager
        async def _empty_discovery() -> AsyncGenerator[Any, None]:
            nonlocal discovery_calls
            discovery_calls += 1

            class _Empty:
                @asynccontextmanager
                async def begin(self) -> AsyncGenerator[None, None]:
                    yield

                async def execute(self, *args: Any, **kwargs: Any) -> _FakeResult:
                    return _FakeResult(rows=[])

            yield _Empty()

        ticks = await run_blocker_detector_loop(
            stop_event,
            interval_seconds=0.0,
            redis=redis_mock,
            worker_id="worker-test-1",
            heartbeat_path=hb_file,
            max_iterations=1,
            run_immediately=True,
            discovery_session_factory=_empty_discovery,
        )
        assert ticks == 1
        assert discovery_calls == 1
        assert hb_file.exists()
        assert await redis_mock.get(BLOCKER_CRON_LOCK_KEY) == "worker-test-1"

        # A second replica while lock is held skips redundant sweep cleanly
        ticks_2 = await run_blocker_detector_loop(
            asyncio.Event(),
            interval_seconds=0.0,
            redis=redis_mock,
            worker_id="worker-test-2",
            heartbeat_path=hb_file,
            max_iterations=1,
            run_immediately=True,
            discovery_session_factory=_empty_discovery,
        )
        assert ticks_2 == 1
        assert discovery_calls == 1

    def test_migration_b4d7e2f9c1a3_defines_security_definer_and_partial_unique_index(self) -> None:
        """Static verification of Alembic migration security & deduplication SQL contracts."""
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert 'down_revision: Union[str, Sequence[str], None] = "a2c4e6f8b0d1"' in source
        assert "uq_risks_active_condition" in source
        assert "status IN ('OPEN', 'ACKNOWLEDGED')" in source
        assert "CREATE OR REPLACE FUNCTION list_active_tenants" in source
        assert "SECURITY DEFINER" in source
        assert "SET search_path = public, pg_temp" in source
        assert "REVOKE ALL ON FUNCTION list_active_tenants(INT, UUID) FROM PUBLIC;" in source
        assert "GRANT EXECUTE ON FUNCTION list_active_tenants(INT, UUID) TO eng_app_user;" in source
        assert "DROP FUNCTION IF EXISTS list_active_tenants(INT, UUID);" in source


# ==============================================================================
# GROUP 5: LIVE POSTGRESQL 16 + FORCE ROW LEVEL SECURITY INTEGRATION TESTS
# ==============================================================================


async def _register_live_tenant(slug_prefix: str) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"Blocker Org {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"blocker-{unique}@test.org",
        "admin_name": f"Admin {unique}",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register tenant: {resp.text}"
        return uuid.UUID(resp.json()["tenant_id"])


@skip_without_live_db
@pytest.mark.asyncio
async def test_live_pg_security_definer_function_and_partial_unique_index_exist() -> None:
    """TC-BLOCKER-005 & TC-BLOCKER-016: Verify pg_proc SECURITY DEFINER & uq_risks_active_condition."""
    registration_rate_limiter.reset()
    async with async_engine.connect() as conn:
        fn_row = (
            await conn.execute(
                text(
                    "SELECT proname, prosecdef, proconfig "
                    "FROM pg_proc WHERE proname = 'list_active_tenants';"
                )
            )
        ).fetchone()
        assert fn_row is not None, "list_active_tenants RPC missing from PostgreSQL"
        assert fn_row[1] is True, "list_active_tenants must be SECURITY DEFINER"
        assert fn_row[2] and any("search_path=public, pg_temp" in cfg for cfg in fn_row[2])

        idx_row = (
            await conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'uq_risks_active_condition';"
                )
            )
        ).fetchone()
        assert idx_row is not None, "uq_risks_active_condition partial unique index missing"
        assert "UNIQUE INDEX" in idx_row[1]


@skip_without_live_db
@pytest.mark.asyncio
async def test_live_pg_partial_unique_index_suppresses_concurrent_duplicates() -> None:
    """TC-BLOCKER-005: Real PostgreSQL ON CONFLICT DO NOTHING enforces strictly 1 Risk + 1 OutboxEvent."""
    registration_rate_limiter.reset()
    tid = await _register_live_tenant("blocker-conc")
    now = datetime.now(timezone.utc)
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    pr_id = uuid.uuid4()

    async with get_tenant_db_context(tid) as session:
        session.add(
            Project(
                id=proj_id,
                tenant_id=tid,
                name="Conc Proj",
                slug=f"conc-{proj_id.hex[:8]}",
                pr_stale_threshold_hours=48,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 1_000_000_000),
                full_name=f"org/conc-{repo_id.hex[:6]}",
                created_at=now,
            )
        )
        session.add(
            PullRequest(
                id=pr_id,
                tenant_id=tid,
                project_id=proj_id,
                repository_id=repo_id,
                author_pseudonym_id="a" * 64,
                external_pr_number=501,
                title="Concurrent stale PR",
                state="OPEN",
                head_branch="feat/conc",
                base_branch="main",
                head_sha="a" * 40,
                created_in_git_at=now - timedelta(hours=60),
                updated_at=now - timedelta(hours=60),
                is_stale=False,
            )
        )

    # Race two tenant sweeps concurrently against PostgreSQL
    res_a, res_b = await asyncio.gather(
        sweep_all_active_tenants(now=now, tenant_ids=[tid]),
        sweep_all_active_tenants(now=now, tenant_ids=[tid]),
    )
    assert res_a.stale_pr_risks_created + res_b.stale_pr_risks_created == 1
    assert res_a.outbox_events_staged + res_b.outbox_events_staged == 1

    async with get_tenant_db_context(tid) as session:
        risks = (
            (await session.execute(select(Risk).where(Risk.project_id == proj_id))).scalars().all()
        )
        outboxes = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == RISK_DETECTED_EVENT_TYPE)
                )
            )
            .scalars()
            .all()
        )
        assert len(risks) == 1
        assert len(outboxes) == 1


@skip_without_live_db
@pytest.mark.asyncio
async def test_live_pg_multi_tenant_rls_sweep_and_full_lifecycle_e2e() -> None:
    """TC-BLOCKER-001, 006, 007, 009, 011, 014, 015, 016: Full live PostgreSQL RLS end-to-end check."""
    registration_rate_limiter.reset()
    tid_a = await _register_live_tenant("blocker-rls-a")
    tid_b = await _register_live_tenant("blocker-rls-b")
    now = datetime.now(timezone.utc)

    # Tenant A: 1 stale PR (>24h custom threshold); Tenant B: 3 incidents in 60m
    proj_a = uuid.uuid4()
    repo_a = uuid.uuid4()
    pr_a = uuid.uuid4()
    proj_b = uuid.uuid4()

    async with get_tenant_db_context(tid_a) as session:
        session.add(
            Project(
                id=proj_a,
                tenant_id=tid_a,
                name="Alpha Project",
                slug=f"alpha-{proj_a.hex[:8]}",
                pr_stale_threshold_hours=24,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            Repository(
                id=repo_a,
                tenant_id=tid_a,
                project_id=proj_a,
                external_repo_id=int(uuid.uuid4().int % 1_000_000_000),
                full_name=f"alpha/repo-{repo_a.hex[:6]}",
                created_at=now,
            )
        )
        session.add(
            PullRequest(
                id=pr_a,
                tenant_id=tid_a,
                project_id=proj_a,
                repository_id=repo_a,
                author_pseudonym_id="b" * 64,
                external_pr_number=71,
                title="Stale Alpha PR",
                state="OPEN",
                head_branch="feat/alpha",
                base_branch="main",
                head_sha="b" * 40,
                created_in_git_at=now - timedelta(hours=30),
                updated_at=now - timedelta(hours=30),
                is_stale=False,
            )
        )

    async with get_tenant_db_context(tid_b) as session:
        session.add(
            Project(
                id=proj_b,
                tenant_id=tid_b,
                name="Beta Project",
                slug=f"beta-{proj_b.hex[:8]}",
                pr_stale_threshold_hours=48,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        for mins in (40, 20, 5):
            session.add(
                Incident(
                    id=uuid.uuid4(),
                    tenant_id=tid_b,
                    project_id=proj_b,
                    started_at=now - timedelta(minutes=mins),
                    severity="SEV-1",
                    created_at=now - timedelta(minutes=mins),
                )
            )

    # Verify SECURITY DEFINER active tenant discovery finds both active tenants
    discovered = await discover_active_tenants()
    assert tid_a in discovered and tid_b in discovered

    # Run full sweep across both tenants
    sweep_1 = await sweep_all_active_tenants(now=now, tenant_ids=[tid_a, tid_b])
    assert sweep_1.stale_pr_risks_created == 1
    assert sweep_1.incident_spike_risks_created == 1
    assert sweep_1.outbox_events_staged == 2

    # Repeated sweep emits 0 duplicates
    sweep_2 = await sweep_all_active_tenants(
        now=now + timedelta(minutes=15), tenant_ids=[tid_a, tid_b]
    )
    assert sweep_2.total_risks_created == 0
    assert sweep_2.outbox_events_staged == 0
    assert sweep_2.duplicates_suppressed == 2

    # RLS Isolation: Tenant A only sees its own STALE_PR Risk; Tenant B only sees INCIDENT_SPIKE
    async with get_tenant_db_context(tid_a) as session:
        risks_a = (await session.execute(select(Risk))).scalars().all()
        assert len(risks_a) == 1
        assert risks_a[0].type == RiskType.STALE_PR.value
        assert risks_a[0].tenant_id == tid_a
        pr_row = await session.get(PullRequest, pr_a)
        assert pr_row is not None and pr_row.is_stale is True
        # Clean activity reset -> is_stale=False & Risk -> RESOLVED
        await reset_pr_staleness_on_activity(
            session, tenant_id=tid_a, pr=pr_row, activity_at=now + timedelta(minutes=20)
        )

    async with get_tenant_db_context(tid_a) as session:
        pr_row = await session.get(PullRequest, pr_a)
        assert pr_row is not None and pr_row.is_stale is False
        risks_a_after = (await session.execute(select(Risk))).scalars().all()
        assert len(risks_a_after) == 1
        assert risks_a_after[0].status == RiskStatus.RESOLVED.value

    async with get_tenant_db_context(tid_b) as session:
        risks_b = (await session.execute(select(Risk))).scalars().all()
        assert len(risks_b) == 1
        assert risks_b[0].type == RiskType.INCIDENT_SPIKE.value
        assert risks_b[0].tenant_id == tid_b
        # Confirm Tenant B cannot read Tenant A's tenant record
        foreign_tenant = await session.get(Tenant, tid_a)
        assert foreign_tenant is None

    # Atomic rollback check on real PostgreSQL
    proj_c = uuid.uuid4()
    with pytest.raises(RuntimeError, match="forced_rollback"):
        async with get_tenant_db_context(tid_b) as session:
            session.add(
                Project(
                    id=proj_c,
                    tenant_id=tid_b,
                    name="Rollback Proj",
                    slug=f"rb-{proj_c.hex[:8]}",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            risk_id, outbox_ev, created = await upsert_blocker_risk_with_outbox(
                session,
                tenant_id=tid_b,
                project_id=proj_c,
                risk_type=RiskType.INCIDENT_SPIKE,
                severity=RiskSeverity.CRITICAL,
                subject_type=RiskSubjectType.PROJECT,
                subject_id=proj_c,
                outbox_payload={"incident_count": 3},
                detected_at=now,
            )
            assert created is True and risk_id is not None and outbox_ev is not None
            raise RuntimeError("forced_rollback")

    async with get_tenant_db_context(tid_b) as session:
        assert await session.get(Project, proj_c) is None
        rb_risks = (
            (await session.execute(select(Risk).where(Risk.project_id == proj_c))).scalars().all()
        )
        assert rb_risks == []
