"""
SCRUM-422 (Task 24.1) & SCRUM-423 (Task 24.2):
Automated tests verifying monotonic version extraction rules for GitHub, Jira, and Slack,
plus conditional SQL update guards (`WHERE entity_version < :incoming_version` and
`ON CONFLICT DO UPDATE ... WHERE entity_version < EXCLUDED.entity_version`) across
`pull_requests`, `issues`, and `communication_events`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

import pytest
from sqlalchemy import select, text

from app.db.models.models import (
    CommunicationEvent,
    Project,
    PullRequest,
    Repository,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.git.normalization import normalize_pull_request
from app.git.service import process_pull_request
from app.jira.normalization import normalize_jira_event
from app.jira.service import upsert_issue
from app.webhooks.slack_processor import upsert_communication_event_versioned
from app.webhooks.versioning import (
    execute_versioned_update,
    extract_entity_version,
    extract_github_pr_version,
    extract_jira_issue_version,
    extract_slack_event_version,
)


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
# SCRUM-422: Canonical Version Extraction Rules Unit Tests
# ==============================================================================


def test_github_pr_version_extraction_epoch_ms_and_fallbacks() -> None:
    payload_updated = {
        "action": "synchronize",
        "pull_request": {
            "number": 10,
            "created_at": "2026-11-19T10:00:00Z",
            "updated_at": "2026-11-19T12:34:56.789Z",
        },
    }
    ver = extract_github_pr_version(payload_updated)
    expected_ms = int(
        datetime(2026, 11, 19, 12, 34, 56, 789000, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert ver == expected_ms
    assert extract_entity_version("github", payload_updated) == expected_ms

    # Fallback to created_at on initial open payload without updated_at
    payload_created = {
        "action": "opened",
        "pull_request": {
            "number": 10,
            "created_at": "2026-11-19T10:00:00Z",
        },
    }
    created_ms = extract_github_pr_version(payload_created)
    assert created_ms is not None and created_ms < expected_ms


def test_jira_issue_version_extraction_changelog_id_and_updated_fallback() -> None:
    # Primary: changelog.id integer
    payload_with_cl = {
        "webhookEvent": "jira:issue_updated",
        "changelog": {"id": "40512", "items": []},
        "issue": {
            "key": "MANTA-42",
            "fields": {"updated": "2026-11-19T11:00:00.000+0000"},
        },
    }
    assert extract_jira_issue_version(payload_with_cl) == 40512
    assert extract_entity_version("jira", payload_with_cl) == 40512

    # Fallback when changelog absent: issue.fields.updated -> UTC epoch ms
    payload_no_cl = {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "key": "MANTA-42",
            "fields": {"updated": "2026-11-19T11:00:00.250+0000"},
        },
    }
    expected_ms = int(
        datetime(2026, 11, 19, 11, 0, 0, 250000, tzinfo=timezone.utc).timestamp() * 1000
    )
    assert extract_jira_issue_version(payload_no_cl) == expected_ms


def test_slack_event_ts_exact_microsecond_precision() -> None:
    # Decimal precision prevents IEEE-754 float drift on 6 decimal microsecond digits
    payload = {
        "type": "event_callback",
        "event": {
            "type": "message",
            "channel": "C123456",
            "ts": "1731326400.123456",
            "event_ts": "1731326400.987654",
        },
    }
    assert extract_slack_event_version(payload) == 1731326400987654
    assert extract_entity_version("slack", payload) == 1731326400987654

    # Fallback to event_time seconds -> microseconds
    payload_event_time = {"type": "event_callback", "event_time": 1731326500}
    assert extract_slack_event_version(payload_event_time) == 1731326500000000


# ==============================================================================
# SCRUM-423: Conditional SQL Updates Across pull_requests, issues, communication_events
# ==============================================================================


class _MockVersionedSession:
    """In-memory mock session simulating `UPDATE ... WHERE entity_version < :incoming_version`."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}

    def seed(self, table: str, rid: uuid.UUID, tid: uuid.UUID, ver: int, title: str) -> None:
        self.rows[(table, rid)] = {"tenant_id": tid, "entity_version": ver, "title": title}

    async def execute(self, stmt: Any) -> Any:
        compiled = stmt.compile()
        cparams = compiled.params
        table_name = stmt.table.name
        rid = cparams["id_1"]
        tid = cparams["tenant_id_1"]
        threshold = cparams["entity_version_1"]
        new_ver = cparams["entity_version"]

        class _R:
            def __init__(self, val: Any) -> None:
                self._val = val

            def scalar_one_or_none(self) -> Any:
                return self._val

        row = self.rows.get((table_name, rid))
        if row and row["tenant_id"] == tid and row["entity_version"] < threshold:
            row["entity_version"] = new_ver
            for k in ("title", "status", "kind"):
                if k in cparams:
                    row[k] = cparams[k]
            return _R(rid)
        return _R(None)


@pytest.mark.parametrize("table_name", ["pull_requests", "issues", "communication_events"])
@pytest.mark.asyncio
async def test_conditional_update_rejects_older_version_mock(table_name: str) -> None:
    db = _MockVersionedSession()
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    db.seed(table_name, rid, tid, ver=1000, title="Initial")

    # Newer version 2000 mutates row
    res_new = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        incoming_version=2000,
        values={"title": "Newer Title"}
        if table_name != "communication_events"
        else {"kind": "resolved_thread"},
    )
    assert res_new.applied is True
    assert res_new.stale_rejected is False
    assert db.rows[(table_name, rid)]["entity_version"] == 2000

    # Delayed older version 1500 updates 0 rows and preserves newer state
    res_stale = await execute_versioned_update(
        db,  # type: ignore[arg-type]
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        incoming_version=1500,
        values={"title": "Stale Overwrite"}
        if table_name != "communication_events"
        else {"kind": "stale"},
    )
    assert res_stale.applied is False
    assert res_stale.stale_rejected is True
    assert db.rows[(table_name, rid)]["entity_version"] == 2000


@pytest.mark.asyncio
async def test_live_pg_out_of_order_rejection_github_jira_slack() -> None:
    """
    Live PostgreSQL E2E verification of SCRUM-422 & SCRUM-423 across:
    1. GitHub `pull_requests`
    2. Jira `issues`
    3. Slack `communication_events`
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host.")

    tid = await _provision_test_tenant("ooo")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()

    async with get_tenant_db_context(tid) as db:
        db.add(Project(id=proj_id, tenant_id=tid, name="OOO Proj", slug=f"ooop-{proj_id.hex[:6]}"))
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name="acme/ooo-repo",
            )
        )

    # 1. GitHub Pull Request out-of-order test
    async with get_tenant_db_context(tid) as db:
        pr_newer = normalize_pull_request(
            {
                "action": "synchronize",
                "repository": {"full_name": "acme/ooo-repo"},
                "pull_request": {
                    "number": 77,
                    "title": "Latest PR Title",
                    "state": "open",
                    "head": {"ref": "feat/ooo", "sha": "b" * 40},
                    "base": {"ref": "main"},
                    "user": {"login": "octo"},
                    "created_at": "2026-11-19T08:00:00Z",
                    "updated_at": "2026-11-19T12:00:00Z",
                },
            }
        )
        res1 = await process_pull_request(db, tenant_id=tid, n=pr_newer)
        assert res1.startswith("persisted_pr:")

        # Delayed older synchronize payload arrives AFTER newer state was persisted
        pr_older = normalize_pull_request(
            {
                "action": "synchronize",
                "repository": {"full_name": "acme/ooo-repo"},
                "pull_request": {
                    "number": 77,
                    "title": "Stale Delayed PR Title",
                    "state": "open",
                    "head": {"ref": "feat/ooo", "sha": "a" * 40},
                    "base": {"ref": "main"},
                    "user": {"login": "octo"},
                    "created_at": "2026-11-19T08:00:00Z",
                    "updated_at": "2026-11-19T09:00:00Z",
                },
            }
        )
        res2 = await process_pull_request(db, tenant_id=tid, n=pr_older)
        assert res2 == "noop:stale_pr_version:77"

        pr_row = (
            await db.execute(
                select(PullRequest).where(
                    PullRequest.tenant_id == tid, PullRequest.external_pr_number == 77
                )
            )
        ).scalar_one()
        assert pr_row.title == "Latest PR Title"
        assert pr_row.head_sha == "b" * 40
        assert pr_row.entity_version == pr_newer.incoming_version

    # 2. Jira Issue out-of-order test
    async with get_tenant_db_context(tid) as db:
        ev_newer = normalize_jira_event(
            "jira:issue_updated",
            {
                "webhookEvent": "jira:issue_updated",
                "changelog": {"id": "500", "items": []},
                "issue": {
                    "id": "8801",
                    "key": "MANTA-888",
                    "fields": {
                        "summary": "Authoritative Newer Summary",
                        "status": {"name": "Done"},
                        "issuetype": {"name": "Story"},
                        "updated": "2026-11-19T12:00:00.000+0000",
                    },
                },
            },
        )
        assert ev_newer.issue is not None
        _, applied_1 = await upsert_issue(db, tenant_id=tid, project_id=proj_id, n=ev_newer.issue)
        assert applied_1 is True

        # Stale out-of-order update with older changelog.id=300
        ev_older = normalize_jira_event(
            "jira:issue_updated",
            {
                "webhookEvent": "jira:issue_updated",
                "changelog": {"id": "300", "items": []},
                "issue": {
                    "id": "8801",
                    "key": "MANTA-888",
                    "fields": {
                        "summary": "Stale Summary",
                        "status": {"name": "To Do"},
                        "issuetype": {"name": "Story"},
                        "updated": "2026-11-19T09:00:00.000+0000",
                    },
                },
            },
        )
        assert ev_older.issue is not None
        issue_row, applied_2 = await upsert_issue(
            db, tenant_id=tid, project_id=proj_id, n=ev_older.issue
        )
        assert applied_2 is False
        assert issue_row.title == "Authoritative Newer Summary"
        assert issue_row.status == "Done"
        assert issue_row.entity_version == 500

    # 3. Slack CommunicationEvent out-of-order test
    async with get_tenant_db_context(tid) as db:
        slack_newer = await upsert_communication_event_versioned(
            db,
            tenant_id=tid,
            project_id=proj_id,
            integration_id=None,
            channel_id="C999",
            thread_ts="1731326400.000001",
            incoming_version=1731326500999999,
            kind="blocker_resolved",
            is_resolved=True,
        )
        assert slack_newer.applied is True

        slack_stale = await upsert_communication_event_versioned(
            db,
            tenant_id=tid,
            project_id=proj_id,
            integration_id=None,
            channel_id="C999",
            thread_ts="1731326400.000001",
            incoming_version=1731326450000000,
            kind="blocker_open",
            is_resolved=False,
        )
        assert slack_stale.applied is False
        assert slack_stale.stale_rejected is True

        comm_row = (
            await db.execute(
                select(CommunicationEvent).where(
                    CommunicationEvent.tenant_id == tid,
                    CommunicationEvent.channel_id == "C999",
                )
            )
        ).scalar_one()
        assert comm_row.is_resolved is True
        assert comm_row.entity_version == 1731326500999999
