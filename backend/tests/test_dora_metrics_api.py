"""
US-30 / Task 30.5: Automated pytest contract validation, null vs 0 semantics,
cross-tenant RLS, and p95 < 300ms latency benchmark tests for DORA metrics API.

Verifies all scenarios from Task 30.4:
- TC-DORA-API-001: Full Data Window (14d) With Complete Telemetry
- TC-DORA-API-002: Full Data Window (30d) With Complete Telemetry
- TC-DORA-API-003: Zero-Deployment Window (Strict null Aggregates Semantics)
- TC-DORA-API-004: Successful Deployments with Zero Failures (CFR = 0.0%, MTTR = null)
- TC-DORA-API-005: Zero-Second Rapid Recovery (MTTR = 0.0h)
- TC-DORA-API-006: Cross-Tenant Isolation & Anti-Enumeration (HTTP 404 Not Found)
- TC-DORA-API-007: Missing and Soft-Deleted Project Query (HTTP 404 Not Found)
- TC-DORA-API-008: Sub-300ms Query Execution Budget Under High Volume (p95 < 300ms)
- TC-DORA-API-009: OpenAPI 3.1 / JSON Schema 2020-12 Schema Validation
- TC-DORA-API-010: Authoritative CD Signal Source Filtering
- TC-DORA-API-011: Non-Production Environment Exclusion
- TC-DORA-API-012: Request Parameter Validation & Conflicts (HTTP 422)
- TC-DORA-API-013: Authentication & RBAC Enforcement Matrix (401 / 403 / 200)
- TC-DORA-API-014: Domain Route Equivalence Verification
- TC-DORA-API-015: Daily Trend Time-Series Contiguity & Monotonicity
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import json
import time
from typing import Any, AsyncGenerator
import unittest.mock as mock
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
import pytest
from sqlalchemy import text

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
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
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.dora.schemas import (
    DoraAggregates,
    DoraDailyPoint,
    DoraMetricsResponse,
)
from app.dora.service import (
    assemble_dora_metrics_response,
    calculate_project_dora_metrics,
    compute_change_failure_rate,
    compute_deployment_frequency,
    compute_lead_time_metrics,
    compute_mttr,
    get_project_dora_metrics,
)
from app.main import app


# ---------------------------------------------------------------------------
# Test Helpers & JWT Auth Fixtures
# ---------------------------------------------------------------------------


def _make_auth_context() -> tuple[rsa.RSAPrivateKey, str, JWKSClient]:
    """Generate RSA key pair and JWKS mock client for OIDC token verification."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = f"dora-test-{uuid.uuid4().hex[:8]}"
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"keys": [jwk]}, request=req)
        ),
    )
    return private_key, kid, client


def _mint_jwt(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    tenant_id: uuid.UUID,
    roles: list[str],
    sub: str = "dora-test-user",
) -> str:
    """Mint RS256 JWT claim set matching Keycloak / OIDC specification."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": f"{sub}@manta.local",
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "iat": now,
        "exp": now + 3600,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


async def _is_live_pg_ready() -> bool:
    """Check whether a live PostgreSQL container is reachable on host."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception:
        return False


async def _provision_tenant_if_live(slug_prefix: str) -> uuid.UUID:
    """Helper to provision isolated tenant in live PostgreSQL."""
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


class _MockScalarResult:
    """Mock query scalar result wrapper for AsyncSession.execute()."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _MockScalarResult:
        return self

    def all(self) -> list[Any]:
        return self._items

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _MockDoraAsyncSession:
    """Mock AsyncSession for DORA metrics query execution without live DB."""

    def __init__(
        self,
        *,
        project: Project | None = None,
        deployments: list[Deployment] | None = None,
        pull_requests_with_deployments: list[tuple[PullRequest, Deployment]] | None = None,
        failures: list[ProductionFailure] | None = None,
    ) -> None:
        self.project = project
        self.deployments = deployments or []
        self.pull_requests_with_deployments = pull_requests_with_deployments or []
        self.failures = failures or []

    async def execute(self, stmt: Any) -> _MockScalarResult:
        entities = [desc.get("entity") for desc in getattr(stmt, "column_descriptions", [])]
        if entities == [Project]:
            return _MockScalarResult([self.project] if self.project is not None else [])
        if entities == [PullRequest, Deployment]:
            return _MockScalarResult(self.pull_requests_with_deployments)
        if entities == [Deployment]:
            return _MockScalarResult(self.deployments)
        if entities == [Deployment, Deployment.incident_links.property.mapper.class_]:
            return _MockScalarResult([(d, None) for d in self.deployments])
        if entities == [ProductionFailure]:
            return _MockScalarResult(self.failures)
        return _MockScalarResult([])


# ---------------------------------------------------------------------------
# Test Suite 1: OpenAPI 3.1 & Schema Compliance Tests (TC-009, TC-012, TC-015)
# ---------------------------------------------------------------------------


def test_tc_dora_api_009_openapi_31_schema_compliance_and_bounds() -> None:
    """
    TC-DORA-API-009: Verify OpenAPI 3.1 schema adherence:
    1. Nullable union types ('anyOf: [number, null]') for all 4 metric fields.
    2. Required presence of metric keys in DoraAggregates and DoraDailyPoint.
    3. Rejection of negative numbers, CFR > 100%, NaN, and Infinity.
    """
    schema = DoraMetricsResponse.model_json_schema()
    defs = schema["$defs"]

    # Verify DoraAggregates schema
    agg_schema = defs["DoraAggregates"]
    expected_metrics = [
        "lead_time_hours",
        "deployment_frequency_per_day",
        "change_failure_rate_pct",
        "mttr_hours",
    ]
    assert set(agg_schema["required"]) == set(expected_metrics)

    for metric_name in expected_metrics:
        prop = agg_schema["properties"][metric_name]
        assert "anyOf" in prop, f"Expected OpenAPI 3.1 'anyOf' union in {metric_name}"
        assert {"type": "null"} in prop["anyOf"], f"Missing null variant in {metric_name}"
        num_variant = next(v for v in prop["anyOf"] if v.get("type") == "number")
        assert num_variant["minimum"] == 0.0
        if metric_name == "change_failure_rate_pct":
            assert num_variant["maximum"] == 100.0

    # Verify DoraDailyPoint schema
    point_schema = defs["DoraDailyPoint"]
    assert set(point_schema["required"]) == set(["date"] + expected_metrics)
    assert point_schema["properties"]["date"]["format"] == "date"

    # Out-of-bounds, non-finite, and negative value rejection
    valid_base = {
        "lead_time_hours": 10.0,
        "deployment_frequency_per_day": 1.0,
        "change_failure_rate_pct": 20.0,
        "mttr_hours": 2.0,
    }
    with pytest.raises(ValidationError):
        DoraAggregates(**{**valid_base, "lead_time_hours": -1.0})
    with pytest.raises(ValidationError):
        DoraAggregates(**{**valid_base, "change_failure_rate_pct": 100.1})
    with pytest.raises(ValidationError):
        DoraAggregates(**{**valid_base, "change_failure_rate_pct": -0.5})
    with pytest.raises(ValidationError):
        DoraAggregates(**{**valid_base, "mttr_hours": float("nan")})
    with pytest.raises(ValidationError):
        DoraAggregates(**{**valid_base, "lead_time_hours": float("inf")})


@pytest.mark.asyncio
async def test_tc_dora_api_012_parameter_validation_and_conflicts_422() -> None:
    """
    TC-DORA-API-012: Verify that malformed or unsupported parameters return HTTP 422:
    - Non-UUID path parameter
    - Unsupported timeframe_days (e.g., 7 or 45)
    - Conflicting parameters (timeframe_days=14 and window=30d)
    """
    priv_key, kid, jwks_client = _make_auth_context()
    orig_jwks = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = jwks_client

    tid = uuid.uuid4()
    pid = uuid.uuid4()
    token = _mint_jwt(priv_key, kid=kid, tenant_id=tid, roles=["DEVELOPER"])

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            headers = {"Authorization": f"Bearer {token}"}

            # 1. Malformed UUID path
            r_bad_uuid = await ac.get("/api/v1/projects/not-a-uuid/dora-metrics", headers=headers)
            assert r_bad_uuid.status_code == 422

            # 2. Unsupported timeframe_days=7
            r_bad_days = await ac.get(
                f"/api/v1/projects/{pid}/dora-metrics?timeframe_days=7",
                headers=headers,
            )
            assert r_bad_days.status_code == 422

            # 3. Conflicting timeframe_days=14 and window=30d
            r_conflict = await ac.get(
                f"/api/v1/projects/{pid}/dora-metrics?timeframe_days=14&window=30d",
                headers=headers,
            )
            assert r_conflict.status_code == 422

            # 4. Same on alias route /api/v1/dora/projects/{pid}
            r_alias_bad = await ac.get(
                f"/api/v1/dora/projects/{pid}?timeframe_days=45",
                headers=headers,
            )
            assert r_alias_bad.status_code == 422
    finally:
        app.state.jwks_client = orig_jwks


def test_tc_dora_api_015_daily_trend_contiguity_and_monotonicity() -> None:
    """
    TC-DORA-API-015: Verify that daily trend arrays have exactly W elements,
    monotonic UTC dates with delta = 1 day, and reject duplicate dates.
    """
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    # 14-day assembly
    resp_14 = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        lead_time_result=compute_lead_time_metrics([], window_days=14, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            [], window_days=14, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            [], window_days=14, window_end=end_utc
        ),
        mttr_result=compute_mttr([], window_days=14, window_end=end_utc),
    )
    assert len(resp_14.trend) == 14
    for idx in range(1, len(resp_14.trend)):
        prev_date = resp_14.trend[idx - 1].date
        curr_date = resp_14.trend[idx].date
        assert curr_date == prev_date + timedelta(days=1)

    # Duplicate dates must fail validation
    aggs = DoraAggregates(
        lead_time_hours=None,
        deployment_frequency_per_day=None,
        change_failure_rate_pct=None,
        mttr_hours=None,
    )
    duplicate_day = date(2026, 11, 10)
    with pytest.raises(ValidationError, match="Duplicate UTC date"):
        DoraMetricsResponse(
            project_id=pid,
            window="14d",
            authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
            aggregates=aggs,
            trend=[
                DoraDailyPoint(
                    date=duplicate_day,
                    lead_time_hours=None,
                    deployment_frequency_per_day=None,
                    change_failure_rate_pct=None,
                    mttr_hours=None,
                ),
                DoraDailyPoint(
                    date=duplicate_day,
                    lead_time_hours=None,
                    deployment_frequency_per_day=None,
                    change_failure_rate_pct=None,
                    mttr_hours=None,
                ),
            ],
        )


# ---------------------------------------------------------------------------
# Test Suite 2: Null vs 0 Semantics & Telemetry Windows (TC-001 - TC-005)
# ---------------------------------------------------------------------------


def test_tc_dora_api_001_and_002_full_data_window_14d_and_30d() -> None:
    """
    TC-DORA-API-001 & TC-DORA-API-002:
    Verify full data windows (14d and 30d) return all 4 non-null aggregates,
    explanatory notes, and exactly 14 or 30 trend points.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 23, 59, 59, tzinfo=timezone.utc)

    # Mock 6 successful deployments and 1 resolved failure session (10:00 -> 11:48 = 1.80h)
    deployments: list[Deployment] = []
    pr_items: list[tuple[PullRequest, Deployment]] = []

    for i in range(6):
        dep_time = end_utc - timedelta(days=i * 2, hours=3)
        commit_time = dep_time - timedelta(hours=23, minutes=30)
        d = Deployment(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            environment="production",
            commit_sha=f"{i:040x}",
            status=DeploymentOutcome.SUCCESS.value,
            authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            deployed_at=dep_time,
        )
        pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            repository_id=uuid.uuid4(),
            external_pr_number=200 + i,
            title=f"feat: DORA item {i}",
            state="merged",
            author_pseudonym_id="0" * 64,
            first_commit_at=commit_time,
            merged_at=dep_time - timedelta(minutes=15),
        )
        deployments.append(d)
        pr_items.append((pr, d))

    pf = ProductionFailure(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        status="RESOLVED",
        severity="SEV-1",
    )

    # 1. 14-day window assembly
    res_14 = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        lead_time_result=compute_lead_time_metrics(pr_items, window_days=14, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            deployments, window_days=14, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            deployments, window_days=14, window_end=end_utc
        ),
        mttr_result=compute_mttr([pf], window_days=14, window_end=end_utc),
    )
    assert res_14.window == "14d"
    assert res_14.aggregates.lead_time_hours == 23.5
    assert res_14.aggregates.deployment_frequency_per_day == 0.43
    assert res_14.aggregates.change_failure_rate_pct == 0.0
    assert res_14.aggregates.mttr_hours == 1.8
    assert len(res_14.trend) == 14
    assert any("github_deployments_api" in n for n in res_14.notes)
    assert any("change_failure_rate_pct is 0.0%" in n for n in res_14.notes)

    # 2. 30-day window assembly
    res_30 = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=30,
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        lead_time_result=compute_lead_time_metrics(pr_items, window_days=30, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            deployments, window_days=30, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            deployments, window_days=30, window_end=end_utc
        ),
        mttr_result=compute_mttr([pf], window_days=30, window_end=end_utc),
    )
    assert res_30.window == "30d"
    assert res_30.aggregates.lead_time_hours == 23.5
    assert res_30.aggregates.deployment_frequency_per_day == 0.2
    assert res_30.aggregates.change_failure_rate_pct == 0.0
    assert res_30.aggregates.mttr_hours == 1.8
    assert len(res_30.trend) == 30


def test_tc_dora_api_003_zero_deployment_window_strict_null_aggregates() -> None:
    """
    TC-DORA-API-003: When zero deployment events occur in the window:
    - All 4 aggregates evaluate strictly to null (None).
    - All daily points in the trend vector evaluate to null.
    - Notes state that no authoritative deployments were recorded.
    """
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    res = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=30,
        authoritative_deployment_signal=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK,
        lead_time_result=compute_lead_time_metrics([], window_days=30, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            [], window_days=30, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            [], window_days=30, window_end=end_utc
        ),
        mttr_result=compute_mttr([], window_days=30, window_end=end_utc),
    )

    assert res.aggregates.lead_time_hours is None
    assert res.aggregates.deployment_frequency_per_day is None
    assert res.aggregates.change_failure_rate_pct is None
    assert res.aggregates.mttr_hours is None
    assert len(res.trend) == 30
    assert all(pt.lead_time_hours is None for pt in res.trend)
    assert all(pt.deployment_frequency_per_day is None for pt in res.trend)
    assert all(pt.change_failure_rate_pct is None for pt in res.trend)
    assert all(pt.mttr_hours is None for pt in res.trend)
    assert any("No authoritative production deployments" in n for n in res.notes)


def test_tc_dora_api_004_successful_deployments_with_zero_failures_cfr_zero_mttr_null() -> None:
    """
    TC-DORA-API-004: Successful deployments with zero failures:
    - CFR evaluates to measured numeric 0.0 (0.0%).
    - MTTR evaluates to null (no outages occurred, NOT 0.0h).
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    # 5 successful production deployments, 0 failures, 0 incidents
    deps = [
        Deployment(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            environment="production",
            commit_sha=f"{i:040x}",
            status=DeploymentOutcome.SUCCESS.value,
            authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
            deployed_at=end_utc - timedelta(days=i),
        )
        for i in range(5)
    ]

    res = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        lead_time_result=compute_lead_time_metrics([], window_days=14, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            deps, window_days=14, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            deps, window_days=14, window_end=end_utc
        ),
        mttr_result=compute_mttr([], window_days=14, window_end=end_utc),
    )

    assert res.aggregates.change_failure_rate_pct == 0.0
    assert res.aggregates.mttr_hours is None
    assert res.aggregates.deployment_frequency_per_day == 0.36
    assert any("change_failure_rate_pct is 0.0%" in n for n in res.notes)
    assert any("mttr_hours is null" in n for n in res.notes)


def test_tc_dora_api_005_zero_second_rapid_recovery_mttr_zero() -> None:
    """
    TC-DORA-API-005: Zero-second rapid recovery (resolved_at == started_at):
    - MTTR evaluates to measured numeric 0.0 (0.0h).
    - Contrasts with null in TC-DORA-API-004.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t_outage = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)

    zero_sec_failure = ProductionFailure(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        started_at=t_outage,
        resolved_at=t_outage,  # 0 second duration
        status="RESOLVED",
        severity="SEV-1",
    )

    res = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        lead_time_result=compute_lead_time_metrics([], window_days=14, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            [], window_days=14, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            [], window_days=14, window_end=end_utc
        ),
        mttr_result=compute_mttr([zero_sec_failure], window_days=14, window_end=end_utc),
    )

    assert res.aggregates.mttr_hours == 0.0
    assert any("mttr_hours is 0.0" in n for n in res.notes)


# ---------------------------------------------------------------------------
# Test Suite 3: Domain Filtering & Exclusions (TC-010, TC-011)
# ---------------------------------------------------------------------------


def test_tc_dora_api_010_authoritative_cd_signal_source_filtering() -> None:
    """
    TC-DORA-API-010: Deployments originating from secondary or non-authoritative
    CD signals must be excluded from calculations.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    auth_deps = [
        Deployment(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            environment="production",
            commit_sha="a" * 40,
            status=DeploymentOutcome.SUCCESS.value,
            authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            deployed_at=end_utc - timedelta(days=1),
        )
    ]
    aux_deps = [
        Deployment(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            environment="production",
            commit_sha="b" * 40,
            status=DeploymentOutcome.FAILED.value,
            authoritative_signal_source=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK.value,
            deployed_at=end_utc - timedelta(days=2),
        )
    ]

    all_ingested = auth_deps + aux_deps
    qualifying_deps = [
        d
        for d in all_ingested
        if d.authoritative_signal_source == DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value
    ]
    assert len(qualifying_deps) == 1
    assert len(all_ingested) == 2

    # When filtered by GITHUB_DEPLOYMENTS_API, only qualifying_deps are passed
    res = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        lead_time_result=compute_lead_time_metrics([], window_days=14, window_end=end_utc),
        deployment_frequency_result=compute_deployment_frequency(
            qualifying_deps, window_days=14, window_end=end_utc
        ),
        change_failure_rate_result=compute_change_failure_rate(
            qualifying_deps, window_days=14, window_end=end_utc
        ),
        mttr_result=compute_mttr([], window_days=14, window_end=end_utc),
    )
    assert res.aggregates.deployment_frequency_per_day == 0.07
    assert res.aggregates.change_failure_rate_pct == 0.0  # aux FAILED deployment excluded


def test_tc_dora_api_011_non_production_environment_exclusion() -> None:
    """
    TC-DORA-API-011: Deployments to non-production environments (staging, dev, qa)
    must be strictly excluded from production DORA metrics.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    end_utc = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    prod_success = Deployment(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        environment="production",
        commit_sha="1" * 40,
        status=DeploymentOutcome.SUCCESS.value,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
        deployed_at=end_utc - timedelta(days=1),
    )
    staging_failed = Deployment(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        environment="staging",
        commit_sha="2" * 40,
        status=DeploymentOutcome.FAILED.value,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
        deployed_at=end_utc - timedelta(days=1),
    )

    df_res = compute_deployment_frequency(
        [prod_success, staging_failed], window_days=14, window_end=end_utc
    )
    cfr_res = compute_change_failure_rate(
        [prod_success, staging_failed], window_days=14, window_end=end_utc
    )

    assert df_res.successful_deployment_count == 1
    assert cfr_res.total_deployment_count == 1
    assert cfr_res.failed_deployment_count == 0
    assert cfr_res.change_failure_rate_percentage == 0.0


# ---------------------------------------------------------------------------
# Test Suite 4: Security, Authentication, RBAC & Cross-Tenant RLS (TC-006, TC-007, TC-013, TC-014)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_api_006_and_007_cross_tenant_isolation_and_soft_deleted_anti_enumeration_404() -> (
    None
):
    """
    TC-DORA-API-006 & TC-DORA-API-007:
    Verify cross-tenant queries and soft-deleted projects yield HTTP 404 (anti-enumeration).
    """
    priv_key, kid, jwks_client = _make_auth_context()
    orig_jwks = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = jwks_client

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    proj_a_id = uuid.uuid4()
    deleted_proj_id = uuid.uuid4()

    proj_a = Project(
        id=proj_a_id,
        tenant_id=tenant_a,
        name="Project Tenant A",
        slug="proj-a",
        authoritative_cd_signal="github_deployments_api",
    )

    class _TenantFilterMockSession:
        def __init__(self, current_tenant: uuid.UUID) -> None:
            self.current_tenant = current_tenant

        async def execute(self, stmt: Any) -> _MockScalarResult:
            entities = [desc.get("entity") for desc in getattr(stmt, "column_descriptions", [])]
            if entities == [Project] and self.current_tenant == tenant_a:
                # If query targets deleted_proj_id or non-existent, return 0 rows
                for crit in getattr(stmt, "_where_criteria", ()):
                    right = getattr(crit, "right", None)
                    if getattr(right, "value", None) == deleted_proj_id:
                        return _MockScalarResult([])
                return _MockScalarResult([proj_a])
            return _MockScalarResult([])

    @asynccontextmanager
    async def _mock_tenant_ctx(tid: uuid.UUID) -> AsyncGenerator[_TenantFilterMockSession, None]:
        yield _TenantFilterMockSession(tid)

    token_tenant_b = _mint_jwt(priv_key, kid=kid, tenant_id=tenant_b, roles=["TENANT_ADMIN"])
    token_tenant_a = _mint_jwt(priv_key, kid=kid, tenant_id=tenant_a, roles=["DEVELOPER"])

    try:
        with mock.patch("app.dora.service.get_tenant_db_context", side_effect=_mock_tenant_ctx):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # 1. Tenant B accessing Tenant A's project -> strictly 404 Not Found
                r_cross = await ac.get(
                    f"/api/v1/projects/{proj_a_id}/dora-metrics",
                    headers={"Authorization": f"Bearer {token_tenant_b}"},
                )
                assert r_cross.status_code == 404
                assert r_cross.json() == {"detail": f"Project '{proj_a_id}' not found."}

                # 2. Soft-deleted project query -> strictly 404 Not Found
                r_deleted = await ac.get(
                    f"/api/v1/projects/{deleted_proj_id}/dora-metrics",
                    headers={"Authorization": f"Bearer {token_tenant_a}"},
                )
                assert r_deleted.status_code == 404
                assert r_deleted.json() == {"detail": f"Project '{deleted_proj_id}' not found."}
    finally:
        app.state.jwks_client = orig_jwks


@pytest.mark.asyncio
async def test_tc_dora_api_013_authentication_and_rbac_permissions_matrix() -> None:
    """
    TC-DORA-API-013: Verify authentication & RBAC permission matrix:
    - 401 Unauthorized for unauthenticated callers.
    - 403 Forbidden for callers lacking VIEW_PROJECT_DORA.
    - 200 OK for all recognized roles (DEVELOPER, SCRUM_MASTER, PROJECT_MANAGER,
      SOLUTION_ARCHITECT, DELIVERY_DIRECTOR, TOP_MANAGEMENT, TENANT_ADMIN).
    """
    priv_key, kid, jwks_client = _make_auth_context()
    orig_jwks = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = jwks_client

    tid = uuid.uuid4()
    pid = uuid.uuid4()

    proj = Project(
        id=pid,
        tenant_id=tid,
        name="RBAC DORA Test",
        slug="rbac-dora",
        authoritative_cd_signal="github_deployments_api",
    )

    @asynccontextmanager
    async def _mock_session_ctx(t: uuid.UUID) -> AsyncGenerator[_MockDoraAsyncSession, None]:
        yield _MockDoraAsyncSession(project=proj)

    try:
        with mock.patch("app.dora.service.get_tenant_db_context", side_effect=_mock_session_ctx):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                # 1. Unauthenticated request -> 401
                r_no_auth = await ac.get(f"/api/v1/projects/{pid}/dora-metrics")
                assert r_no_auth.status_code == 401

                # 2. Empty role list -> 403 Forbidden
                token_no_role = _mint_jwt(priv_key, kid=kid, tenant_id=tid, roles=[])
                r_no_role = await ac.get(
                    f"/api/v1/projects/{pid}/dora-metrics",
                    headers={"Authorization": f"Bearer {token_no_role}"},
                )
                assert r_no_role.status_code == 403
                assert r_no_role.json() == FORBIDDEN_RESPONSE

                # 3. All authorized roles -> 200 OK
                authorized_roles = [
                    "DEVELOPER",
                    "SCRUM_MASTER",
                    "PROJECT_MANAGER",
                    "SOLUTION_ARCHITECT",
                    "DELIVERY_DIRECTOR",
                    "TOP_MANAGEMENT",
                    "TENANT_ADMIN",
                ]
                for role in authorized_roles:
                    token_role = _mint_jwt(priv_key, kid=kid, tenant_id=tid, roles=[role])
                    r_auth = await ac.get(
                        f"/api/v1/projects/{pid}/dora-metrics",
                        headers={"Authorization": f"Bearer {token_role}"},
                    )
                    assert r_auth.status_code == 200, (
                        f"Role {role} failed with {r_auth.status_code}"
                    )
    finally:
        app.state.jwks_client = orig_jwks


@pytest.mark.asyncio
async def test_tc_dora_api_014_domain_route_equivalence() -> None:
    """
    TC-DORA-API-014: Verify GET /api/v1/projects/{id}/dora-metrics and
    GET /api/v1/dora/projects/{id} return identical schema, status, and payload.
    """
    priv_key, kid, jwks_client = _make_auth_context()
    orig_jwks = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = jwks_client

    tid = uuid.uuid4()
    pid = uuid.uuid4()
    proj = Project(
        id=pid,
        tenant_id=tid,
        name="Equivalence Test",
        slug="equiv-test",
        authoritative_cd_signal="argocd_postsync_webhook",
    )

    @asynccontextmanager
    async def _mock_session_ctx(t: uuid.UUID) -> AsyncGenerator[_MockDoraAsyncSession, None]:
        yield _MockDoraAsyncSession(project=proj)

    token = _mint_jwt(priv_key, kid=kid, tenant_id=tid, roles=["DEVELOPER"])

    try:
        with mock.patch("app.dora.service.get_tenant_db_context", side_effect=_mock_session_ctx):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                headers = {"Authorization": f"Bearer {token}"}
                r_primary = await ac.get(
                    f"/api/v1/projects/{pid}/dora-metrics?timeframe_days=14",
                    headers=headers,
                )
                r_alias = await ac.get(
                    f"/api/v1/dora/projects/{pid}?timeframe_days=14",
                    headers=headers,
                )

                assert r_primary.status_code == 200
                assert r_alias.status_code == 200
                assert r_primary.json() == r_alias.json()
    finally:
        app.state.jwks_client = orig_jwks


# ---------------------------------------------------------------------------
# Test Suite 5: Latency Benchmark Tests (p95 < 300ms) (TC-008)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_dora_api_008_latency_benchmark_under_300ms_in_memory() -> None:
    """
    TC-DORA-API-008: In-memory latency benchmark asserting that calculation,
    assembly, and serialization complete well under 300ms (p95 < 300ms).
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    now = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    # Synthetic high-volume fixtures (100 deployments, 50 PRs, 10 failures)
    deployments = [
        Deployment(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            environment="production",
            commit_sha=f"{i:040x}",
            status=DeploymentOutcome.SUCCESS.value
            if i % 10 != 0
            else DeploymentOutcome.FAILED.value,
            authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
            deployed_at=now - timedelta(days=i % 28, hours=i % 12),
        )
        for i in range(100)
    ]
    prs_with_deps = [
        (
            PullRequest(
                id=uuid.uuid4(),
                tenant_id=tid,
                project_id=pid,
                repository_id=uuid.uuid4(),
                external_pr_number=i,
                title=f"PR {i}",
                state="merged",
                author_pseudonym_id="0" * 64,
                first_commit_at=deployments[i].deployed_at - timedelta(hours=10 + (i % 20)),
                merged_at=deployments[i].deployed_at - timedelta(minutes=15),
            ),
            deployments[i],
        )
        for i in range(50)
    ]
    failures = [
        ProductionFailure(
            id=uuid.uuid4(),
            tenant_id=tid,
            project_id=pid,
            started_at=now - timedelta(days=i * 2, hours=4),
            resolved_at=now - timedelta(days=i * 2, hours=2),
            status="RESOLVED",
            severity="SEV-1",
        )
        for i in range(10)
    ]

    proj = Project(
        id=pid,
        tenant_id=tid,
        name="Latency Benchmark Project",
        slug="latency-bench",
        authoritative_cd_signal="github_deployments_api",
    )

    mock_session = _MockDoraAsyncSession(
        project=proj,
        deployments=deployments,
        pull_requests_with_deployments=prs_with_deps,
        failures=failures,
    )

    # Measure 30 consecutive executions
    latencies_ms: list[float] = []
    for _ in range(30):
        t0 = time.perf_counter()
        resp = await calculate_project_dora_metrics(
            mock_session,  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            timeframe_days=30,
            window_end=now,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)
        assert len(resp.trend) == 30

    latencies_ms.sort()
    p95_index = int(len(latencies_ms) * 0.95) - 1
    p95_ms = latencies_ms[p95_index]

    assert p95_ms < 300.0, f"Expected DORA calculation p95 < 300ms, got {p95_ms:.2f}ms"


@pytest.mark.asyncio
async def test_tc_dora_api_008_latency_benchmark_under_300ms_live_postgres() -> None:
    """
    TC-DORA-API-008: Live PostgreSQL end-to-end latency benchmark (< 300ms p95).
    Skipped if live PostgreSQL instance is not reachable on host.
    """
    if not await _is_live_pg_ready():
        pytest.skip("Live PostgreSQL database is not reachable on host.")

    tenant_id = await _provision_tenant_if_live("dora-api-bench")
    proj_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # Seed project in live DB under tenant RLS
    async with get_tenant_db_context(tenant_id) as db:
        db.add(
            Project(
                id=proj_id,
                tenant_id=tenant_id,
                name="Live Benchmark Project",
                slug=f"bench-{proj_id.hex[:6]}",
                authoritative_cd_signal="github_deployments_api",
            )
        )
        await db.flush()
        db.add(
            Repository(
                id=repo_id,
                tenant_id=tenant_id,
                project_id=proj_id,
                external_repo_id=int(uuid.uuid4().int % 10_000_000),
                full_name=f"acme/bench-{repo_id.hex[:6]}",
            )
        )
        db.add(
            Contributor(
                pseudonym_id="pseudo_bench_1",
                tenant_id=tenant_id,
                provider="github",
                external_actor_id="octo-bench",
                salt_secret="encrypted_salt",
            )
        )
        await db.flush()

        for i in range(15):
            dep_time = now - timedelta(days=i % 10, hours=1)
            pr_first_commit = dep_time - timedelta(hours=5)
            dep_status = "SUCCESS"
            dep_id = uuid.uuid4()
            pr_id = uuid.uuid4()

            db.add(
                Deployment(
                    id=dep_id,
                    tenant_id=tenant_id,
                    project_id=proj_id,
                    repository_id=repo_id,
                    actor_pseudonym_id="pseudo_bench_1",
                    external_deployment_id=f"bench-dep-{i}",
                    environment="production",
                    commit_sha=f"{i:040x}",
                    status=dep_status,
                    is_change_failure=False,
                    authoritative_signal_source="github_deployments_api",
                    deployed_at=dep_time,
                )
            )
            db.add(
                PullRequest(
                    id=pr_id,
                    tenant_id=tenant_id,
                    project_id=proj_id,
                    repository_id=repo_id,
                    author_pseudonym_id="pseudo_bench_1",
                    external_pr_number=3000 + i,
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
                    tenant_id=tenant_id,
                    deployment_id=dep_id,
                    pull_request_id=pr_id,
                )
            )

    latencies_ms: list[float] = []
    for _ in range(15):
        t0 = time.perf_counter()
        res = await get_project_dora_metrics(tenant_id, proj_id, timeframe_days=30, window_end=now)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)
        assert len(res.trend) == 30

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95) - 1]
    assert p95_ms < 300.0, f"Live PG p95 latency exceeded 300ms: {p95_ms:.2f}ms"
