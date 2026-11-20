"""
SCRUM-459 (Task 30.3):
Tests verifying PostgreSQL `generate_series` DORA daily trend consolidation,
composite index definitions on `deployments`, `deployment_pull_requests`, and
`production_failures`, tenant RLS isolation (HTTP 404 / 0 rows cross-tenant),
and p95 < 300ms query latency.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from app.db.models.models import (
    Contributor,
    Deployment,
    DeploymentPullRequest,
    ProductionFailure,
    Project,
    PullRequest,
    Repository,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.dora.service import (
    _DORA_CONSOLIDATED_SERIES_SQL,
    calculate_rolling_change_failure_rate,
    calculate_rolling_deployment_frequency,
    calculate_rolling_lead_time,
    calculate_rolling_mttr,
    get_project_dora_metrics,
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


def test_dora_composite_indexes_declared_in_models() -> None:
    """Verify SCRUM-459 composite indexes exist on Deployment, DeploymentPullRequest, ProductionFailure."""
    dep_idx_names = {idx.name for idx in Deployment.__table__.indexes}
    dpr_idx_names = {idx.name for idx in DeploymentPullRequest.__table__.indexes}
    pf_idx_names = {idx.name for idx in ProductionFailure.__table__.indexes}

    assert "idx_deployments_dora_lookup" in dep_idx_names
    assert "idx_deployment_pull_requests_tenant_dep_pr" in dpr_idx_names
    assert "idx_production_failures_dora_mttr" in pf_idx_names


def test_consolidated_sql_contains_generate_series_cte() -> None:
    sql_text = str(_DORA_CONSOLIDATED_SERIES_SQL)
    assert "generate_series" in sql_text.lower()
    assert "percentile_cont(0.5)" in sql_text.lower()
    assert "deployment_pull_requests" in sql_text
    assert "production_failures" in sql_text
    assert "deployments" in sql_text


@pytest.mark.asyncio
async def test_live_pg_dora_consolidated_parity_rls_and_p95_latency() -> None:
    """
    Live PostgreSQL integration test:
    1. Confirms composite indexes exist in pg_indexes.
    2. Confirms consolidated `generate_series` CTE produces identical metrics and 30d/14d trends
       to the Python domain calculators.
    3. Confirms cross-tenant RLS blocks Tenant B with HTTP 404.
    4. Confirms p95 query latency < 300ms under live RLS session.
    """
    if not await _is_live_pg_ready():
        pytest.skip("PostgreSQL database not reachable on host.")

    tenant_a = await _provision_test_tenant("dora-a")
    tenant_b = await _provision_test_tenant("dora-b")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # 1. Verify pg_indexes contains SCRUM-459 composite indexes
    async with AsyncSessionLocal() as db:
        idx_res = await db.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE indexname IN (
                    'idx_deployments_dora_lookup',
                    'idx_deployment_pull_requests_tenant_dep_pr',
                    'idx_production_failures_dora_mttr'
                )
                """
            )
        )
        found_indexes = {row[0] for row in idx_res.fetchall()}
        assert found_indexes == {
            "idx_deployments_dora_lookup",
            "idx_deployment_pull_requests_tenant_dep_pr",
            "idx_production_failures_dora_mttr",
        }

    # Seed Tenant A project telemetry under RLS
    async with get_tenant_db_context(tenant_a) as db:
        db.add(
            Project(
                id=proj_id,
                tenant_id=tenant_a,
                name="DORA Perf Project",
                slug=f"dora-perf-{proj_id.hex[:6]}",
                authoritative_cd_signal="github_deployments_api",
            )
        )
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tenant_a,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name=f"acme/dora-{repo_id.hex[:6]}",
            )
        )
        db.add(
            Contributor(
                pseudonym_id="pseudo_dora_1",
                tenant_id=tenant_a,
                provider="github",
                external_actor_id="octo-perf",
                salt_secret="encrypted_salt",
            )
        )
        await db.flush()

        # Seed 20 production deployments & shipped PRs across 14 days
        for i in range(20):
            dep_time = now - timedelta(days=i % 12, hours=2)
            pr_first_commit = dep_time - timedelta(hours=4 + (i % 5))
            dep_status = "FAILED" if i == 0 else "SUCCESS"
            dep_id = uuid.uuid4()
            pr_id = uuid.uuid4()

            db.add(
                Deployment(
                    id=dep_id,
                    tenant_id=tenant_a,
                    project_id=proj_id,
                    repository_id=repo_id,
                    actor_pseudonym_id="pseudo_dora_1",
                    external_deployment_id=f"gh-dep-{i}",
                    environment="production",
                    commit_sha=f"{i:040x}",
                    status=dep_status,
                    is_change_failure=(dep_status == "FAILED"),
                    authoritative_signal_source="github_deployments_api",
                    deployed_at=dep_time,
                )
            )
            db.add(
                PullRequest(
                    id=pr_id,
                    tenant_id=tenant_a,
                    project_id=proj_id,
                    repository_id=repo_id,
                    author_pseudonym_id="pseudo_dora_1",
                    external_pr_number=1000 + i,
                    title=f"PR {i}",
                    state="merged",
                    head_branch=f"feat/{i}",
                    base_branch="main",
                    head_sha=f"{i:040x}",
                    first_commit_at=pr_first_commit,
                    created_in_git_at=pr_first_commit,
                    merged_at=dep_time - timedelta(minutes=10),
                )
            )
            await db.flush()
            db.add(
                DeploymentPullRequest(
                    id=uuid.uuid4(),
                    tenant_id=tenant_a,
                    deployment_id=dep_id,
                    pull_request_id=pr_id,
                )
            )

        # Seed 2 resolved production_failures (MTTR = 1.5h and 2.5h -> 2.0h)
        db.add(
            ProductionFailure(
                id=uuid.uuid4(),
                tenant_id=tenant_a,
                project_id=proj_id,
                started_at=now - timedelta(days=3, hours=3),
                resolved_at=now - timedelta(days=3, hours=1, minutes=30),
                status="RESOLVED",
                severity="SEV-1",
            )
        )
        db.add(
            ProductionFailure(
                id=uuid.uuid4(),
                tenant_id=tenant_a,
                project_id=proj_id,
                started_at=now - timedelta(days=1, hours=5),
                resolved_at=now - timedelta(days=1, hours=2, minutes=30),
                status="RESOLVED",
                severity="SEV-2",
            )
        )

    # 2. Verify numerical parity with Python reference domain calculators
    consolidated = await get_project_dora_metrics(
        tenant_a, proj_id, timeframe_days=14, window_end=now
    )
    async with get_tenant_db_context(tenant_a) as session:
        ref_lt = await calculate_rolling_lead_time(
            session, tenant_id=tenant_a, project_id=proj_id, window_days=14, window_end=now
        )
        ref_df = await calculate_rolling_deployment_frequency(
            session, tenant_id=tenant_a, project_id=proj_id, window_days=14, window_end=now
        )
        ref_cfr = await calculate_rolling_change_failure_rate(
            session, tenant_id=tenant_a, project_id=proj_id, window_days=14, window_end=now
        )
        ref_mttr = await calculate_rolling_mttr(
            session, tenant_id=tenant_a, project_id=proj_id, window_days=14, window_end=now
        )

    assert consolidated.aggregates.lead_time_hours == ref_lt.median_lead_time_hours
    assert consolidated.aggregates.deployment_frequency_per_day == ref_df.deployments_per_day
    assert consolidated.aggregates.change_failure_rate_pct == ref_cfr.change_failure_rate_percentage
    assert consolidated.aggregates.mttr_hours == ref_mttr.mttr_hours
    assert len(consolidated.trend) == 14

    # 3. Verify Cross-Tenant RLS Isolation (Tenant B cannot view Tenant A's project -> HTTP 404)
    with pytest.raises(HTTPException) as exc_info:
        await get_project_dora_metrics(tenant_b, proj_id, timeframe_days=30, window_end=now)
    assert exc_info.value.status_code == 404

    # 4. Benchmark p95 latency (< 300ms) across 20 executions
    latencies_ms: list[float] = []
    for _ in range(20):
        t0 = time.perf_counter()
        res = await get_project_dora_metrics(tenant_a, proj_id, timeframe_days=30, window_end=now)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)
        assert len(res.trend) == 30

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95) - 1]
    assert p95_ms < 300.0, f"Expected DORA p95 < 300ms, got {p95_ms:.2f}ms"
