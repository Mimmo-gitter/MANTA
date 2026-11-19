"""
SCRUM-412 (Task 22.4): Automated tests verifying optimistic PostgreSQL fencing token
assertions (`WHERE active_fencing_token <= :worker_claimed_token`), `job_leases.owner_token`
verification, clean rollback on stale write, and `STALE_FENCING_TOKEN_REJECTED` structured logging
across `webhook_events`, `pull_requests`, `issues`, and `deployments`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

import pytest
from sqlalchemy import text

from app.db.models.models import (
    Deployment,
    Issue,
    Project,
    PullRequest,
    Repository,
    WebhookEvent,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from workers.fencing import StaleFencingTokenError, execute_fenced_domain_update


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


class _FakeResult:
    def __init__(self, value: Any = None, row: Any = None) -> None:
        self._value = value
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._value

    def first(self) -> Any:
        return self._row


class _InMemoryFencedSession:
    """In-memory session verifying real SQL UPDATE predicate semantics across domain tables."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, uuid.UUID], dict[str, Any]] = {}
        self.leases: dict[tuple[str, uuid.UUID], int] = {}
        self.rolled_back = False

    def seed_row(
        self, table_name: str, record_id: uuid.UUID, tenant_id: uuid.UUID, token: int
    ) -> None:
        self.rows[(table_name, record_id)] = {
            "tenant_id": tenant_id,
            "active_fencing_token": token,
            "state": "initial",
        }

    def seed_lease(self, job_id: str, tenant_id: uuid.UUID, owner_token: int) -> None:
        self.leases[(job_id, tenant_id)] = owner_token

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql_str = str(stmt)
        if "FROM job_leases" in sql_str and params:
            tok = self.leases.get((params["job_id"], params["tenant_id"]))
            return _FakeResult(row=(tok, "worker") if tok is not None else None)

        compiled = stmt.compile()
        cparams = compiled.params
        table_name = getattr(getattr(stmt, "table", None), "name", None)

        if sql_str.lstrip().upper().startswith("UPDATE"):
            record_id = cparams["id_1"]
            tenant_id = cparams["tenant_id_1"]
            claimed_tok = cparams["active_fencing_token_1"]
            row = self.rows.get((table_name, record_id))
            if row and row["tenant_id"] == tenant_id and row["active_fencing_token"] <= claimed_tok:
                row["active_fencing_token"] = cparams.get("active_fencing_token", claimed_tok)
                if "status" in cparams:
                    row["status"] = cparams["status"]
                if "state" in cparams:
                    row["state"] = cparams["state"]
                return _FakeResult(value=record_id)
            return _FakeResult(value=None)

        if sql_str.lstrip().upper().startswith("SELECT"):
            # Fallback select for existing persisted token
            table_name = stmt.column_descriptions[0]["entity"].__tablename__
            record_id = cparams["id_1"]
            tenant_id = cparams["tenant_id_1"]
            row = self.rows.get((table_name, record_id))
            if row and row["tenant_id"] == tenant_id:
                return _FakeResult(value=row["active_fencing_token"])
            return _FakeResult(value=None)

        return _FakeResult(value=None)


@pytest.mark.parametrize(
    ("table_name", "update_vals"),
    [
        ("webhook_events", {"status": "completed"}),
        ("pull_requests", {"state": "merged"}),
        ("issues", {"status": "Done"}),
        ("deployments", {"status": "SUCCESS"}),
    ],
)
@pytest.mark.asyncio
async def test_fenced_domain_update_rejects_token_1_after_token_2_with_structured_log(
    caplog: pytest.LogCaptureFixture,
    table_name: str,
    update_vals: dict[str, Any],
) -> None:
    """
    Acceptance Criteria (SCRUM-412):
    A worker attempting to write with token = 1 after token = 2 has already updated the target
    record mutates 0 rows, rolls back transaction, emits STALE_FENCING_TOKEN_REJECTED log,
    and raises StaleFencingTokenError.
    """
    caplog.set_level(logging.WARNING, logger="workers.fencing")
    db = _InMemoryFencedSession()
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    db.seed_row(table_name, rid, tid, token=0)

    # Worker 2 writes with token = 2 -> succeeds
    ok = await execute_fenced_domain_update(
        db,
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        worker_claimed_token=2,
        values=update_vals,
        job_id=f"{table_name}:{rid}",
        worker_id="worker-2",
    )
    assert ok is True
    assert db.rows[(table_name, rid)]["active_fencing_token"] == 2

    # Idempotent retry with token = 2 -> succeeds (<= predicate)
    ok_retry = await execute_fenced_domain_update(
        db,
        table_name=table_name,  # type: ignore[arg-type]
        record_id=rid,
        tenant_id=tid,
        worker_claimed_token=2,
        values=update_vals,
        job_id=f"{table_name}:{rid}",
        worker_id="worker-2",
    )
    assert ok_retry is True

    # Stale Worker 1 attempts write with token = 1 after token = 2 -> rejected!
    caplog.clear()
    with pytest.raises(StaleFencingTokenError) as exc_info:
        await execute_fenced_domain_update(
            db,
            table_name=table_name,  # type: ignore[arg-type]
            record_id=rid,
            tenant_id=tid,
            worker_claimed_token=1,
            values={"status" if "status" in update_vals else "state": "stale_corrupt"},
            job_id=f"{table_name}:{rid}",
            worker_id="worker-1-zombie",
        )

    assert exc_info.value.claimed_token == 1
    assert db.rolled_back is True
    assert db.rows[(table_name, rid)]["active_fencing_token"] == 2
    assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_fenced_domain_update_verifies_job_leases_owner_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Checks job_leases.owner_token == :worker_claimed_token before committing mutations."""
    caplog.set_level(logging.WARNING, logger="workers.fencing")
    db = _InMemoryFencedSession()
    tid = uuid.uuid4()
    rid = uuid.uuid4()
    job_id = f"pull_requests:{rid}"

    db.seed_row("pull_requests", rid, tid, token=0)
    db.seed_lease(job_id, tid, owner_token=2)

    with pytest.raises(StaleFencingTokenError):
        await execute_fenced_domain_update(
            db,
            table_name="pull_requests",
            record_id=rid,
            tenant_id=tid,
            worker_claimed_token=1,
            values={"state": "corrupt"},
            job_id=job_id,
            worker_id="worker-1",
            verify_job_lease=True,
        )

    assert db.rolled_back is True
    assert db.rows[("pull_requests", rid)]["active_fencing_token"] == 0
    assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_live_pg_fenced_updates_across_all_domain_tables(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Live PostgreSQL integration check enforcing SCRUM-412 across `webhook_events`,
    `pull_requests`, `issues`, and `deployments` under tenant RLS.
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host.")

    caplog.set_level(logging.WARNING, logger="workers.fencing")
    tid = await _provision_test_tenant("fence")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    wh_id = uuid.uuid4()
    pr_id = uuid.uuid4()
    issue_id = uuid.uuid4()
    dep_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with get_tenant_db_context(tid) as db:
        db.add(Project(id=proj_id, tenant_id=tid, name="Fence Proj", slug=f"fp-{proj_id.hex[:6]}"))
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tid,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name=f"org/fence-{repo_id.hex[:6]}",
            )
        )
        await db.flush()
        db.add(
            WebhookEvent(
                id=wh_id,
                tenant_id=tid,
                provider="github",
                provider_delivery_id=f"deliv-{wh_id.hex}",
                event_type="push",
                payload={"ok": True},
                status="staged",
                active_fencing_token=0,
            )
        )
        db.add(
            PullRequest(
                id=pr_id,
                tenant_id=tid,
                project_id=proj_id,
                repository_id=repo_id,
                author_pseudonym_id="pseudo_1",
                external_pr_number=42,
                title="Test PR",
                state="open",
                head_branch="feat/a",
                base_branch="main",
                head_sha="a" * 40,
                created_in_git_at=now,
                active_fencing_token=0,
            )
        )
        db.add(
            Issue(
                id=issue_id,
                tenant_id=tid,
                project_id=proj_id,
                external_key=f"MANTA-{issue_id.hex[:6]}",
                issue_type="Story",
                title="Test Issue",
                status="To Do",
                active_fencing_token=0,
            )
        )
        db.add(
            Deployment(
                id=dep_id,
                tenant_id=tid,
                project_id=proj_id,
                environment="production",
                commit_sha="b" * 40,
                status="SUCCESS",
                deployed_at=now,
                active_fencing_token=0,
            )
        )

    targets = [
        ("webhook_events", wh_id, {"status": "completed"}, {"status": "failed"}),
        ("pull_requests", pr_id, {"state": "merged"}, {"state": "closed"}),
        ("issues", issue_id, {"status": "Done"}, {"status": "To Do"}),
        ("deployments", dep_id, {"status": "FAILED"}, {"status": "SUCCESS"}),
    ]

    for tbl, rid, win_vals, stale_vals in targets:
        # Token 2 succeeds under tenant context
        async with get_tenant_db_context(tid) as session:
            ok = await execute_fenced_domain_update(
                session,
                table_name=tbl,  # type: ignore[arg-type]
                record_id=rid,
                tenant_id=tid,
                worker_claimed_token=2,
                values=win_vals,
                job_id=f"{tbl}:{rid}",
                worker_id="w2",
            )
            assert ok is True

        # Token 1 after Token 2 fails cleanly, rolls back, logs STALE_FENCING_TOKEN_REJECTED
        caplog.clear()
        with pytest.raises(StaleFencingTokenError):
            async with get_tenant_db_context(tid) as session:
                await execute_fenced_domain_update(
                    session,
                    table_name=tbl,  # type: ignore[arg-type]
                    record_id=rid,
                    tenant_id=tid,
                    worker_claimed_token=1,
                    values=stale_vals,
                    job_id=f"{tbl}:{rid}",
                    worker_id="w1-stale",
                )
        assert any("STALE_FENCING_TOKEN_REJECTED" in rec.message for rec in caplog.records)
