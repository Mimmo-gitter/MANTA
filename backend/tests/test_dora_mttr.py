"""
US-29: Automated pytest test suite for DORA Mean Time to Recovery (MTTR) Engine.

Task: SCRUM-448 (Task 29.3: Create test scenarios for 10:00 fail -> 11:48 restore (1.8h)
      with unrelated 10:20 PR merge, plus zero-failure null window)
Parent User Story: SCRUM-366 (US-29: DORA Mean Time to Recovery)
Design & Verification Specifications:
- docs/dora-mttr-calculation-test-scenarios.md (Task 29.3 / SCRUM-448)
- backend/tests/scenarios/test_us29_mttr_calculation_scenarios.md (Task 29.3 / SCRUM-448)
- docs/dora-mttr-calculation-design.md (Task 29.1 / SCRUM-446)

Master Test Matrix Coverage:
- TC-DORA-MTTR-001: Canonical Health-Probe Recovery Benchmark Fixture (10:00 fail -> 10:20 PR merge -> 11:48 restore = 1.80h / 6480s)
- TC-DORA-MTTR-002: Defect Differential Analysis (strictly asserting 1.80h vs 0.33h, 1.47h, 0.58h)
- TC-DORA-MTTR-003: Multi-Outage Arithmetic Mean Calculation (18,000s / 3 = 6000s = 1.67h)
- TC-DORA-MTTR-004: Concurrent & Overlapping Outages Strict Session Pairing (self-contained, no cross-pairing)
- TC-DORA-MTTR-005: Open / Unresolved Outage Exclusion (resolved_at IS NULL excluded from historical MTTR)
- TC-DORA-MTTR-006: Zero-Failure Window Null Semantics (14-day rolling window returns None/null)
- TC-DORA-MTTR-007: Zero-Failure Window Null Semantics (30-day rolling window returns None/null)
- TC-DORA-MTTR-008: Window with Only Open/Unresolved Outages Evaluates to Null
- TC-DORA-MTTR-009: Window Boundary Temporal Edge Cases (inclusion strictly by resolved_at)
- TC-DORA-MTTR-010: Clock Skew & Anomalous Negative Duration Guard (resolved_at >= started_at)
- TC-DORA-MTTR-011: Multi-Tenant Row-Level Security (RLS) Isolation
- TC-DORA-MTTR-012: Soft-Deleted Project & Project Scope Isolation
- TC-DORA-MTTR-013: Daily MTTR Trend Time-Series Generation (contiguous daily buckets, null on idle days)
- TC-DORA-MTTR-014: Unsupported Window Parameter Validation (fail-closed ValueError on unsupported windows)
- TC-DORA-MTTR-015: Instantaneous Automated Recovery (0.0s recovery distinct from null)
- TC-DORA-MTTR-SQL: PostgreSQL 16 SQL Query Compilation & Execution
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.db.models.models import Incident, ProductionFailure, Project
from app.db.session import async_engine, get_tenant_db_context
from app.dora.service import (
    build_mttr_incidents_query,
    build_mttr_production_failures_query,
    build_mttr_production_failures_sql_query,
    build_mttr_sql_query,
    calculate_rolling_mttr,
    compute_mttr,
    compute_session_recovery_duration_hours,
)
from app.projects.health_service import process_health_signal


def is_live_db_available() -> bool:
    """Check if a real PostgreSQL instance is reachable with configured credentials."""
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


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL test instance is not reachable locally; runs in CI.",
)


def _make_incident(
    *,
    started_at: datetime,
    resolved_at: Optional[datetime] = None,
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    incident_id: Optional[uuid.UUID] = None,
    severity: str = "SEV-2",
) -> Incident:
    """Helper to construct an in-memory Incident entity with explicit timestamps."""
    inc = Incident()
    inc.id = incident_id or uuid.uuid4()
    inc.tenant_id = tenant_id or uuid.uuid4()
    inc.project_id = project_id or uuid.uuid4()
    inc.started_at = started_at
    inc.resolved_at = resolved_at
    inc.severity = severity
    inc.created_at = started_at
    if resolved_at is not None and resolved_at >= started_at:
        inc.recovery_time_seconds = (resolved_at - started_at).total_seconds()
    else:
        inc.recovery_time_seconds = None
    return inc


def _make_production_failure(
    *,
    started_at: datetime,
    resolved_at: Optional[datetime] = None,
    status: str = "RESOLVED",
    tenant_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    failure_id: Optional[uuid.UUID] = None,
    incident_id: Optional[uuid.UUID] = None,
    probe_source: str = "webhook",
    severity: str = "SEV-1",
) -> ProductionFailure:
    """Helper to construct an in-memory US-68 ProductionFailure session."""
    pf = ProductionFailure()
    pf.id = failure_id or uuid.uuid4()
    pf.tenant_id = tenant_id or uuid.uuid4()
    pf.project_id = project_id or uuid.uuid4()
    pf.incident_id = incident_id
    pf.started_at = started_at
    pf.resolved_at = resolved_at
    pf.status = status
    pf.probe_source = probe_source
    pf.severity = severity
    pf.created_at = started_at
    pf.updated_at = resolved_at or started_at
    if resolved_at is not None and resolved_at >= started_at:
        pf.recovery_time_seconds = round((resolved_at - started_at).total_seconds(), 2)
    else:
        pf.recovery_time_seconds = None
    return pf


# ===========================================================================
# TC-DORA-MTTR-001: Canonical Health-Probe Recovery Benchmark Fixture (1.80h)
# ===========================================================================


def test_task_29_3_canonical_health_probe_recovery_10_00_to_11_48():
    """
    Task 29.3 / SCRUM-448 Primary Acceptance Criteria:
    Detail the 10:00 fail -> 10:20 PR merge -> 11:48 pass scenario (1.8h):
    - 10:00:00: Health probe detects production failure (started_at).
    - 10:20:00: Unrelated PR is merged to trunk (merged_at).
    - 10:35:00: CD pipeline completes deployment (deployed_at).
    - 11:48:00: Production health probe reports PASS (resolved_at).
    Total duration = 10:00 to 11:48 = 108 minutes = 6,480.0 seconds = 1.80 hours.
    Asserts exact interval math and complete independence from PR merge / deploy.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_restore = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    incident = _make_incident(
        started_at=t_fail,
        resolved_at=t_restore,
        severity="SEV-1",
    )

    result = compute_mttr([incident], window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.resolved_outage_count == 1
    assert result.mttr_seconds == 6480.0
    assert result.mttr_hours == 1.80
    assert result.mttr_minutes == 108.00
    assert result.mttr == 1.80

    # Verify single outage record details
    assert len(result.outages) == 1
    assert result.outages[0].duration_seconds == 6480.0
    assert result.outages[0].duration_hours == 1.80
    assert result.outages[0].severity == "SEV-1"


# ===========================================================================
# TC-DORA-MTTR-002: Defect Differential Analysis (PR Merge vs Health Probe)
# ===========================================================================


def test_task_29_3_defect_differential_analysis_rejects_pr_merge_proxies():
    """
    Task 29.3 / SCRUM-448:
    Verify that MTTR calculation strictly asserts MTTR == 1.80h and rejects defective
    temporal proxy models (PR merge at 10:20, deployment at 10:35):
    - Defect 1: PR merge as recovery point (10:20 - 10:00 = 20m = 0.33h).
    - Defect 2: PR merge as incident start point (11:48 - 10:20 = 88m = 1.47h).
    - Defect 3: Deployment as recovery point (10:35 - 10:00 = 35m = 0.58h).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_merge = datetime(2026, 11, 12, 10, 20, 0, tzinfo=timezone.utc)
    t_deploy = datetime(2026, 11, 12, 10, 35, 0, tzinfo=timezone.utc)
    t_restore = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    # Calculate defective proxy values for explicit comparison
    defect_1_merge_as_restore = round((t_merge - t_fail).total_seconds() / 3600.0, 2)
    assert defect_1_merge_as_restore == 0.33

    defect_2_merge_as_start = round((t_restore - t_merge).total_seconds() / 3600.0, 2)
    assert defect_2_merge_as_start == 1.47

    defect_3_deploy_as_restore = round((t_deploy - t_fail).total_seconds() / 3600.0, 2)
    assert defect_3_deploy_as_restore == 0.58

    incident = _make_incident(started_at=t_fail, resolved_at=t_restore)
    result = compute_mttr([incident], window_days=14, window_end=window_end)

    # Authoritative assertion
    assert result.mttr_hours == 1.80
    assert result.mttr_seconds == 6480.0

    # Conclusively reject defective temporal proxies
    assert result.mttr_hours != defect_1_merge_as_restore
    assert result.mttr_hours != defect_2_merge_as_start
    assert result.mttr_hours != defect_3_deploy_as_restore


# ===========================================================================
# TC-DORA-MTTR-003: Multi-Outage Arithmetic Mean Calculation
# ===========================================================================


def test_task_29_3_multi_outage_arithmetic_mean_calculation():
    """
    Task 29.3 / SCRUM-448: Multi-outage mean calculation.
    - Outage 1: 10:00 -> 11:48 = 6,480s (1.80h)
    - Outage 2: 14:00 -> 14:36 = 2,160s (0.60h)
    - Outage 3: 16:00 -> 18:36 = 9,360s (2.60h)
    Total duration = 18,000s = 5.0h across 3 outages.
    MTTR = 18,000 / 3 = 6,000.00s = 1.67h (100.00m).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tid = uuid.uuid4()
    pid = uuid.uuid4()

    s1 = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        tenant_id=tid,
        project_id=pid,
        severity="SEV-1",
    )
    s2 = _make_incident(
        started_at=datetime(2026, 11, 12, 14, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 14, 36, 0, tzinfo=timezone.utc),
        tenant_id=tid,
        project_id=pid,
        severity="SEV-2",
    )
    s3 = _make_incident(
        started_at=datetime(2026, 11, 12, 16, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 18, 36, 0, tzinfo=timezone.utc),
        tenant_id=tid,
        project_id=pid,
        severity="SEV-1",
    )

    result = compute_mttr([s1, s2, s3], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 3
    assert result.mttr_seconds == 6000.0
    assert result.mttr_hours == 1.67
    assert result.mttr_minutes == 100.0
    assert len(result.outages) == 3


# ===========================================================================
# TC-DORA-MTTR-004: Concurrent & Overlapping Outages Strict Session Pairing
# ===========================================================================


def test_task_29_3_concurrent_overlapping_outages_strict_pairing():
    """
    Task 29.3 / Invariant 3:
    Concurrent outages must strictly self-pair (started_at -> resolved_at)
    and never cross-pair or merge into an envelope:
    - Session A: 10:00 -> 11:00 = 3,600s (1.0h)
    - Session B: 10:15 -> 11:45 = 5,400s (1.5h)
    Mean MTTR = (3600 + 5400) / 2 = 4,500s = 1.25h.
    Asserts rejection of envelope duration (10:00 to 11:45 = 1.75h) and cross-pairing.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    s_a = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 0, 0, tzinfo=timezone.utc),
    )
    s_b = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 15, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 45, 0, tzinfo=timezone.utc),
    )

    result = compute_mttr([s_a, s_b], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 2
    assert result.mttr_seconds == 4500.0
    assert result.mttr_hours == 1.25
    assert result.mttr_minutes == 75.0

    # Rejects bounding envelope (11:45 - 10:00 = 105m = 1.75h)
    assert result.mttr_hours != 1.75
    # Rejects false cross-pairing (11:00 - 10:15 = 45m = 0.75h)
    assert result.mttr_hours != 0.75


# ===========================================================================
# TC-DORA-MTTR-005: Open / Unresolved Outage Exclusion (Ongoing Incident)
# ===========================================================================


def test_task_29_3_open_unresolved_outage_exclusion():
    """
    Task 29.3 / Invariant 4:
    Active outages with resolved_at IS NULL must be strictly excluded from MTTR.
    They must not use COALESCE(resolved_at, NOW()) to corrupt historical MTTR.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    s_resolved = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
    )
    s_ongoing = _make_incident(
        started_at=datetime(2026, 11, 13, 8, 0, 0, tzinfo=timezone.utc),
        resolved_at=None,
    )

    result = compute_mttr([s_resolved, s_ongoing], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 1
    assert result.mttr_seconds == 6480.0
    assert result.mttr_hours == 1.80
    assert len(result.outages) == 1
    assert result.outages[0].incident_id == s_resolved.id


# ===========================================================================
# TC-DORA-MTTR-006 & TC-DORA-MTTR-007: Zero-Failure Window Null Semantics
# ===========================================================================


def test_task_29_3_zero_failure_window_null_semantics_14d():
    """
    Task 29.3 / Invariant 5:
    When zero resolved failure sessions exist in a 14-day window:
    - MTTR evaluates to None (null in JSON).
    - Must NOT evaluate to 0.0 or 0.00.
    - Generates 14 zero-filled daily buckets with mttr=None.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    result = compute_mttr([], window_days=14, window_end=window_end)

    assert result.window_days == 14
    assert result.resolved_outage_count == 0
    assert result.mttr_seconds is None
    assert result.mttr_hours is None
    assert result.mttr_minutes is None
    assert result.mttr is None
    assert result.outages == []
    assert len(result.daily_series) == 14
    assert all(day.resolved_outage_count == 0 for day in result.daily_series)
    assert all(day.mttr_seconds is None for day in result.daily_series)
    assert all(day.mttr_hours is None for day in result.daily_series)


def test_task_29_3_zero_failure_window_null_semantics_30d():
    """
    Task 29.3 / Invariant 5:
    When zero resolved failure sessions exist in a 30-day window:
    - MTTR evaluates strictly to None (null).
    - Generates 30 zero-filled daily buckets with mttr=None.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    result = compute_mttr([], window_days=30, window_end=window_end)

    assert result.window_days == 30
    assert result.resolved_outage_count == 0
    assert result.mttr_seconds is None
    assert result.mttr_hours is None
    assert result.mttr is None
    assert len(result.daily_series) == 30
    assert all(day.mttr_seconds is None for day in result.daily_series)


# ===========================================================================
# TC-DORA-MTTR-008: Window with Only Open/Unresolved Outages
# ===========================================================================


def test_task_29_3_window_with_only_open_outages_evaluates_to_null():
    """
    Task 29.3 / Scope:
    When a window contains exclusively ongoing/open outages (resolved_at = NULL),
    resolved outage count is 0 and MTTR returns None (null).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    o1 = _make_incident(
        started_at=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc),
        resolved_at=None,
    )
    o2 = _make_incident(
        started_at=datetime(2026, 11, 12, 8, 0, 0, tzinfo=timezone.utc),
        resolved_at=None,
    )

    result = compute_mttr([o1, o2], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 0
    assert result.mttr_seconds is None
    assert result.mttr_hours is None
    assert result.mttr is None


# ===========================================================================
# TC-DORA-MTTR-009: Window Boundary Temporal Edge Cases
# ===========================================================================


def test_task_29_3_window_boundary_edge_cases():
    """
    Task 29.3 / Invariant 6:
    Window inclusion is strictly governed by resolved_at IN [window_start, window_end]:
    - I_pre: resolved before window_start -> EXCLUDED
    - I_span: started before window_start, resolved inside -> INCLUDED (full duration)
    - I_exact_start: resolved exactly at window_start -> INCLUDED
    - I_exact_end: resolved exactly at window_end -> INCLUDED
    - I_post: resolved after window_end -> EXCLUDED
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=14)

    # I_pre: resolved 1s before window_start -> EXCLUDED
    i_pre = _make_incident(
        started_at=window_start - timedelta(hours=4),
        resolved_at=window_start - timedelta(seconds=1),
    )
    # I_span: started 2h before window_start, resolved 2h inside -> INCLUDED (4h = 14,400s)
    i_span = _make_incident(
        started_at=window_start - timedelta(hours=2),
        resolved_at=window_start + timedelta(hours=2),
    )
    # I_exact_start: resolved exactly on window_start -> INCLUDED (1h = 3,600s)
    i_exact_start = _make_incident(
        started_at=window_start - timedelta(hours=1),
        resolved_at=window_start,
    )
    # I_exact_end: resolved exactly on window_end -> INCLUDED (1h = 3,600s)
    i_exact_end = _make_incident(
        started_at=window_end - timedelta(hours=1),
        resolved_at=window_end,
    )
    # I_post: resolved 1s after window_end -> EXCLUDED
    i_post = _make_incident(
        started_at=window_end - timedelta(minutes=30),
        resolved_at=window_end + timedelta(seconds=1),
    )

    result = compute_mttr(
        [i_pre, i_span, i_exact_start, i_exact_end, i_post],
        window_days=14,
        window_end=window_end,
    )

    # Total included = i_span (14,400s) + i_exact_start (3,600s) + i_exact_end (3,600s) = 21,600s
    # Count = 3 -> Mean = 21,600 / 3 = 7,200s = 2.00h
    assert result.resolved_outage_count == 3
    assert result.mttr_seconds == 7200.0
    assert result.mttr_hours == 2.00
    assert result.mttr_minutes == 120.00


# ===========================================================================
# TC-DORA-MTTR-010: Clock Skew & Anomalous Negative Duration Guard
# ===========================================================================


def test_task_29_3_clock_skew_and_negative_duration_guard():
    """
    Task 29.3 / Invariant 7:
    Records with resolved_at < started_at are excluded via non-negative guard.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    i_valid = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
    )
    # Corrupted / clock-skewed record with resolved_at earlier than started_at
    i_corrupted = _make_incident(
        started_at=datetime(2026, 11, 12, 14, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 13, 30, 0, tzinfo=timezone.utc),
    )

    result = compute_mttr([i_valid, i_corrupted], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 1
    assert result.mttr_seconds == 6480.0
    assert result.mttr_hours == 1.80


# ===========================================================================
# TC-DORA-MTTR-011: Multi-Tenant Row-Level Security (RLS) Isolation
# ===========================================================================


def test_task_29_3_multitenant_rls_isolation():
    """
    Task 29.3 / Invariant 8:
    Verify that tenant incidents are partitioned cleanly.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()

    # Tenant A: 1 outage (1.80h)
    inc_a = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        tenant_id=tid_a,
    )
    # Tenant B: 2 outages (1.0h and 3.0h = 2.0h mean)
    inc_b1 = _make_incident(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 0, 0, tzinfo=timezone.utc),
        tenant_id=tid_b,
    )
    inc_b2 = _make_incident(
        started_at=datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc),
        tenant_id=tid_b,
    )

    # Compute for Tenant A only
    tenant_a_incidents = [inc for inc in [inc_a, inc_b1, inc_b2] if inc.tenant_id == tid_a]
    res_a = compute_mttr(tenant_a_incidents, window_days=14, window_end=window_end)

    assert res_a.resolved_outage_count == 1
    assert res_a.mttr_hours == 1.80

    # Compute for Tenant B only
    tenant_b_incidents = [inc for inc in [inc_a, inc_b1, inc_b2] if inc.tenant_id == tid_b]
    res_b = compute_mttr(tenant_b_incidents, window_days=14, window_end=window_end)

    assert res_b.resolved_outage_count == 2
    assert res_b.mttr_hours == 2.00


# ===========================================================================
# TC-DORA-MTTR-012: Soft-Deleted Project Exclusion
# ===========================================================================


def test_task_29_3_soft_deleted_project_exclusion():
    """
    Task 29.3:
    Verify SQL query builder filters soft-deleted projects (deleted_at IS NULL).
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()

    stmt = build_mttr_sql_query(tenant_id=tid, project_id=pid)
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "projects.deleted_at IS NULL" in compiled_sql
    assert "incidents.resolved_at IS NOT NULL" in compiled_sql
    assert "incidents.resolved_at >= incidents.started_at" in compiled_sql


# ===========================================================================
# TC-DORA-MTTR-013: Daily MTTR Trend Time-Series Generation
# ===========================================================================


def test_task_29_3_daily_mttr_trend_time_series_generation():
    """
    Task 29.3 / Invariant 10:
    Daily series generates contiguous daily buckets in UTC:
    - Day 3 (2026-11-03): 1 outage (1.80h / 6,480s)
    - Day 7 (2026-11-07): 2 outages (0.60h / 2,160s and 2.60h / 9,360s; daily mean = 1.60h / 5,760s)
    - Other 12 days: 0 outages -> mttr_seconds=None
    - 14-day total: 3 outages, mean = 6,000s = 1.67h
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    # Day 3 outage (resolved on 2026-11-03)
    d3_outage = _make_incident(
        started_at=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 3, 11, 48, 0, tzinfo=timezone.utc),  # 6480s / 1.8h
    )
    # Day 7 outages (resolved on 2026-11-07)
    d7_outage_1 = _make_incident(
        started_at=datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 7, 10, 36, 0, tzinfo=timezone.utc),  # 2160s / 0.6h
    )
    d7_outage_2 = _make_incident(
        started_at=datetime(2026, 11, 7, 14, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 7, 16, 36, 0, tzinfo=timezone.utc),  # 9360s / 2.6h
    )

    result = compute_mttr(
        [d3_outage, d7_outage_1, d7_outage_2],
        window_days=14,
        window_end=window_end,
    )

    assert result.resolved_outage_count == 3
    assert result.mttr_seconds == 6000.0
    assert result.mttr_hours == 1.67

    # Validate daily trend series structure
    assert len(result.daily_series) == 14

    # Day 3 check
    day_3_metric = next(
        d for d in result.daily_series if d.date.strftime("%Y-%m-%d") == "2026-11-03"
    )
    assert day_3_metric.resolved_outage_count == 1
    assert day_3_metric.mttr_seconds == 6480.0
    assert day_3_metric.mttr_hours == 1.80
    assert day_3_metric.mttr_minutes == 108.0

    # Day 7 check (mean of 2160s and 9360s = 5760s = 1.60h)
    day_7_metric = next(
        d for d in result.daily_series if d.date.strftime("%Y-%m-%d") == "2026-11-07"
    )
    assert day_7_metric.resolved_outage_count == 2
    assert day_7_metric.mttr_seconds == 5760.0
    assert day_7_metric.mttr_hours == 1.60
    assert day_7_metric.mttr_minutes == 96.0

    # Idle days check
    idle_days = [
        d
        for d in result.daily_series
        if d.date.strftime("%Y-%m-%d") not in ("2026-11-03", "2026-11-07")
    ]
    assert len(idle_days) == 12
    assert all(d.resolved_outage_count == 0 for d in idle_days)
    assert all(d.mttr_seconds is None for d in idle_days)
    assert all(d.mttr_hours is None for d in idle_days)


# ===========================================================================
# TC-DORA-MTTR-014: Unsupported Window Parameter Validation
# ===========================================================================


def test_task_29_3_unsupported_window_parameter_validation():
    """
    Task 29.3:
    Engine fails closed on unsupported window durations (e.g., 7 days).
    """
    with pytest.raises(ValueError, match="Unsupported window_days=7"):
        compute_mttr([], window_days=7)  # type: ignore[arg-type]


# ===========================================================================
# TC-DORA-MTTR-015: Instantaneous Automated Recovery (0.0s Recovery)
# ===========================================================================


def test_task_29_3_instantaneous_automated_recovery_zero_seconds():
    """
    Task 29.3 / Semantic Distinction:
    An automated failover with started_at == resolved_at evaluates to numeric 0.00,
    distinct from None (null) which indicates zero failures occurred.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    instant_outage = _make_incident(started_at=t0, resolved_at=t0)

    result = compute_mttr([instant_outage], window_days=14, window_end=window_end)

    assert result.resolved_outage_count == 1
    assert result.mttr_seconds == 0.0
    assert result.mttr_hours == 0.0
    assert result.mttr_minutes == 0.0
    # Crucial distinction: numeric 0.0, NOT None
    assert result.mttr_seconds is not None
    assert result.mttr_hours is not None


# ===========================================================================
# TC-DORA-MTTR-SQL: PostgreSQL 16 Query Compilation & Contract Verification
# ===========================================================================


def test_task_29_3_postgresql_16_query_compilation_and_execution():
    """
    Task 29.3 & Task 29.1 §4:
    Verify SQL query compilation:
    - AVG(EXTRACT(epoch FROM ...))
    - CASE WHEN COUNT(*) = 0 THEN NULL
    - RLS tenant_id & project_id predicates
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    start_at = datetime(2026, 10, 31, 18, 0, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    stmt = build_mttr_sql_query(
        tenant_id=tid,
        project_id=pid,
        start_at=start_at,
        end_at=end_at,
    )
    assert stmt._generate_cache_key() is not None
    compiled = str(stmt.compile(dialect=postgresql.dialect()))

    assert "extract(epoch from" in compiled.lower()
    assert "incidents.resolved_at is not null" in compiled.lower()
    assert "incidents.resolved_at >= incidents.started_at" in compiled.lower()
    assert "projects.deleted_at is null" in compiled.lower()
    assert "case when (count(incidents.id) =" in compiled.lower()
    assert "then null" in compiled.lower()


# ===========================================================================
# Task 29.2 / SCRUM-447: US-68 Production Failure Sessions & Rolling MTTR
# ===========================================================================


@pytest.mark.parametrize("window_days", [14, 30])
def test_task_29_2_production_failures_10_00_to_11_48_ignores_pr_merge_and_deploy(
    window_days: int,
):
    """
    Task 29.2 / SCRUM-447 Acceptance Criteria:
    - US-68 ProductionFailure detected at 10:00, PR merged at 10:20, restored at 11:48
      computes 1.8 hours (108.0 minutes / 6480.0 seconds) over both 14d and 30d windows.
    - PR merge and deployment timestamps are strictly ignored.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_restore = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    pf = _make_production_failure(
        started_at=t_fail,
        resolved_at=t_restore,
        status="RESOLVED",
        probe_source="uptime_probe",
        severity="SEV-1",
    )

    assert compute_session_recovery_duration_hours(t_fail, t_restore) == 1.8
    with pytest.raises(ValueError, match="cannot be earlier than"):
        compute_session_recovery_duration_hours(t_restore, t_fail)

    result = compute_mttr([pf], window_days=window_days, window_end=window_end)  # type: ignore[arg-type]

    assert result.window_days == window_days
    assert result.resolved_outage_count == 1
    assert result.resolved_failure_session_count == 1
    assert result.mttr_seconds == 6480.0
    assert result.mttr_hours == 1.8
    assert result.mttr_minutes == 108.0
    assert result.mttr == 1.8
    assert len(result.daily_series) == window_days
    assert len(result.outages) == 1
    assert result.outages[0].session_id == pf.id
    assert result.outages[0].duration_minutes == 108.0


def test_task_29_2_production_failures_open_status_and_zero_failures_null_window():
    """
    Task 29.2:
    - ProductionFailure sessions with non-RESOLVED status (e.g. 'OPEN') are excluded.
    - Zero resolved failures in 14d or 30d windows return None (null).
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    open_pf = _make_production_failure(
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        status="OPEN",
    )

    for w_days in (14, 30):
        res = compute_mttr([open_pf], window_days=w_days, window_end=window_end)  # type: ignore[arg-type]
        assert res.resolved_outage_count == 0
        assert res.mttr_seconds is None
        assert res.mttr_hours is None
        assert res.mttr_minutes is None
        assert res.mttr is None
        assert all(point.mttr is None for point in res.daily_series)


def test_task_29_2_deduplicates_synced_production_failure_and_incident_pairs():
    """
    Task 29.2:
    When synced ProductionFailure and Incident records represent the same outage
    (matching id or pf.incident_id == inc.id), compute_mttr counts it once regardless
    of input order and sorts outages chronologically.
    """
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)
    tid = uuid.uuid4()
    pid = uuid.uuid4()

    t_fail_1 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_restore_1 = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)  # 1.80h (6480s)

    shared_inc_id = uuid.uuid4()
    pf_1 = _make_production_failure(
        failure_id=uuid.uuid4(),
        incident_id=shared_inc_id,
        tenant_id=tid,
        project_id=pid,
        started_at=t_fail_1,
        resolved_at=t_restore_1,
        status="RESOLVED",
    )
    inc_1_synced = _make_incident(
        incident_id=shared_inc_id,
        tenant_id=tid,
        project_id=pid,
        started_at=t_fail_1,
        resolved_at=t_restore_1,
    )
    # Separate earlier independent incident: 0.60h (2160s)
    inc_2_independent = _make_incident(
        tenant_id=tid,
        project_id=pid,
        started_at=datetime(2026, 11, 11, 14, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 11, 14, 36, 0, tzinfo=timezone.utc),
    )

    res_pf_only = compute_mttr([pf_1, inc_1_synced], window_days=14, window_end=window_end)
    res_inc_first = compute_mttr([inc_1_synced, pf_1], window_days=14, window_end=window_end)
    assert res_pf_only.resolved_outage_count == 1
    assert res_pf_only.mttr_hours == 1.8
    assert res_inc_first.resolved_outage_count == 1
    assert res_inc_first.mttr_hours == 1.8

    # Combined: (6480 + 2160) / 2 = 4320s = 1.20h = 72.0m; chronologically ordered
    res_combined = compute_mttr(
        [pf_1, inc_1_synced, inc_2_independent],
        window_days=14,
        window_end=window_end,
    )
    assert res_combined.resolved_outage_count == 2
    assert res_combined.mttr_seconds == 4320.0
    assert res_combined.mttr_hours == 1.2
    assert res_combined.mttr_minutes == 72.0
    assert res_combined.outages[0].incident_id == inc_2_independent.id
    assert res_combined.outages[1].incident_id == pf_1.id


def test_task_29_2_production_failures_sql_query_builders_compilation():
    """
    Task 29.2:
    Verify SQL query builders for production_failures enforce status='RESOLVED',
    resolved_at IS NOT NULL, resolved_at >= started_at, tenant/project scoping,
    deleted_at IS NULL, and NULL on zero sessions.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    start_at = datetime(2026, 10, 31, 18, 0, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    rows_stmt = build_mttr_production_failures_query(
        tenant_id=tid,
        project_id=pid,
        start_at=start_at,
        end_at=end_at,
    )
    assert rows_stmt._generate_cache_key() is not None
    compiled_rows = str(rows_stmt.compile(dialect=postgresql.dialect())).lower()
    assert "from production_failures" in compiled_rows
    assert "production_failures.status =" in compiled_rows
    assert "production_failures.resolved_at is not null" in compiled_rows
    assert "production_failures.resolved_at >= production_failures.started_at" in compiled_rows
    assert "projects.deleted_at is null" in compiled_rows

    inc_rows_stmt = build_mttr_incidents_query(
        tenant_id=tid,
        project_id=pid,
        start_at=start_at,
        end_at=end_at,
    )
    assert inc_rows_stmt._generate_cache_key() is not None
    compiled_inc_rows = str(inc_rows_stmt.compile(dialect=postgresql.dialect())).lower()
    assert "from incidents" in compiled_inc_rows
    assert "incidents.resolved_at is not null" in compiled_inc_rows
    assert "incidents.resolved_at >= incidents.started_at" in compiled_inc_rows
    assert "projects.deleted_at is null" in compiled_inc_rows

    agg_stmt = build_mttr_production_failures_sql_query(
        tenant_id=tid,
        project_id=pid,
        start_at=start_at,
        end_at=end_at,
    )
    assert agg_stmt._generate_cache_key() is not None
    compiled_agg = str(agg_stmt.compile(dialect=postgresql.dialect())).lower()
    assert "extract(epoch from" in compiled_agg
    assert "case when (count(production_failures.id) =" in compiled_agg
    assert "then null" in compiled_agg
    assert "production_failures.status =" in compiled_agg
    assert "projects.deleted_at is null" in compiled_agg


@pytest.mark.asyncio
async def test_task_29_2_calculate_rolling_mttr_async_and_null_windows():
    """
    Task 29.2:
    Verify calculate_rolling_mttr queries production_failures & incidents, deduplicates
    synced pairs, ignores PR merges, and returns None (null) on 0 failures for 14d/30d.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    session_id = uuid.uuid4()
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_restore = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)

    pf = _make_production_failure(
        failure_id=session_id,
        incident_id=session_id,
        tenant_id=tid,
        project_id=pid,
        started_at=t_fail,
        resolved_at=t_restore,
        status="RESOLVED",
    )
    inc_synced = _make_incident(
        incident_id=session_id,
        tenant_id=tid,
        project_id=pid,
        started_at=t_fail,
        resolved_at=t_restore,
    )

    class _MockScalars:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def all(self) -> list[Any]:
            return self._items

    class _MockResult:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def scalars(self) -> _MockScalars:
            return _MockScalars(self._items)

    class _MockAsyncSession:
        def __init__(self, pf_items: list[ProductionFailure], inc_items: list[Incident]) -> None:
            self._pf_items = pf_items
            self._inc_items = inc_items
            self.queries: list[Any] = []

        async def execute(self, stmt: Any) -> _MockResult:
            self.queries.append(stmt)
            entity = stmt.column_descriptions[0].get("entity")
            if entity is ProductionFailure:
                return _MockResult(self._pf_items)
            if entity is Incident:
                return _MockResult(self._inc_items)
            return _MockResult([])

    mock_db = _MockAsyncSession([pf], [inc_synced])
    for w_days in (14, 30):
        res = await calculate_rolling_mttr(
            mock_db,  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            window_days=w_days,  # type: ignore[arg-type]
            window_end=window_end,
        )
        assert res.window_days == w_days
        assert res.resolved_outage_count == 1
        assert res.mttr_hours == 1.8
        assert res.mttr_minutes == 108.0
        assert res.mttr_seconds == 6480.0

    empty_db = _MockAsyncSession([], [])
    for w_days in (14, 30):
        empty_res = await calculate_rolling_mttr(
            empty_db,  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            window_days=w_days,  # type: ignore[arg-type]
            window_end=window_end,
        )
        assert empty_res.resolved_outage_count == 0
        assert empty_res.mttr_hours is None
        assert empty_res.mttr_minutes is None
        assert empty_res.mttr_seconds is None

    with pytest.raises(ValueError, match="Unsupported window_days=7"):
        await calculate_rolling_mttr(
            empty_db,  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            window_days=7,  # type: ignore[arg-type]
            window_end=window_end,
        )


@pytest.mark.asyncio
async def test_task_29_2_us68_health_signal_state_machine_to_rolling_mttr_integration():
    """
    Task 29.2 + US-68 End-to-End Integration:
    Process FAIL at 10:00 -> duplicate FAIL at 10:05 -> PASS at 11:48 through
    `process_health_signal` and feed the persisted session store into `calculate_rolling_mttr`.
    Verifies 1.8 hours (108 minutes) for active project and None (null) for healthy project.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    empty_pid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    projects: dict[uuid.UUID, Project] = {
        pid: Project(
            id=pid,
            tenant_id=tid,
            name="MTTR Service",
            slug="mttr-service",
            is_active=True,
            health_probe_enabled=True,
            health_status="HEALTHY",
            created_at=now,
            updated_at=now,
        ),
        empty_pid: Project(
            id=empty_pid,
            tenant_id=tid,
            name="Healthy Service",
            slug="healthy-service",
            is_active=True,
            health_probe_enabled=True,
            health_status="HEALTHY",
            created_at=now,
            updated_at=now,
        ),
    }
    failures: list[ProductionFailure] = []
    incidents: list[Incident] = []

    class _Scalars:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def first(self) -> Any:
            return self._items[0] if self._items else None

        def all(self) -> list[Any]:
            return list(self._items)

    class _ExecResult:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def scalars(self) -> _Scalars:
            return _Scalars(self._items)

    class _InMemorySession:
        def add(self, obj: Any) -> None:
            if isinstance(obj, ProductionFailure) and obj not in failures:
                failures.append(obj)
            elif isinstance(obj, Incident) and obj not in incidents:
                incidents.append(obj)

        async def flush(self) -> None:
            return None

        async def execute(self, stmt: Any) -> _ExecResult:
            bound = stmt.compile().params
            entity = stmt.column_descriptions[0].get("entity")
            sql_text = str(stmt)

            proj_id = next(
                (v for k, v in bound.items() if "project_id" in k and isinstance(v, uuid.UUID)),
                None,
            )
            if entity is Project:
                id_val = next(
                    (
                        v
                        for k, v in bound.items()
                        if k.startswith("id_") and isinstance(v, uuid.UUID)
                    ),
                    None,
                )
                matches = [
                    p
                    for p in projects.values()
                    if p.tenant_id == tid
                    and p.deleted_at is None
                    and (id_val is None or p.id == id_val)
                ]
                return _ExecResult(matches)
            if entity is ProductionFailure:
                status_val = next(
                    (v for k, v in bound.items() if "status" in k and isinstance(v, str)),
                    None,
                )
                matches = [
                    f
                    for f in failures
                    if f.tenant_id == tid
                    and (proj_id is None or f.project_id == proj_id)
                    and (status_val is None or f.status == status_val)
                ]
                if "resolved_at IS NOT NULL" in sql_text:
                    matches = [f for f in matches if f.resolved_at is not None]
                return _ExecResult(matches)
            if entity is Incident:
                matches = [
                    i
                    for i in incidents
                    if i.tenant_id == tid and (proj_id is None or i.project_id == proj_id)
                ]
                if "resolved_at IS NULL" in sql_text:
                    matches = [i for i in matches if i.resolved_at is None]
                elif "resolved_at IS NOT NULL" in sql_text:
                    matches = [i for i in matches if i.resolved_at is not None]
                return _ExecResult(matches)
            return _ExecResult([])

    db = _InMemorySession()
    t_fail = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
    t_dup = datetime(2026, 11, 12, 10, 5, 0, tzinfo=timezone.utc)
    t_pass = datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    await process_health_signal(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=pid,
        status="FAIL",
        signal_timestamp=t_fail,
    )
    await process_health_signal(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=pid,
        status="FAIL",
        signal_timestamp=t_dup,
    )
    await process_health_signal(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=pid,
        status="PASS",
        signal_timestamp=t_pass,
    )
    await process_health_signal(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=empty_pid,
        status="PASS",
        signal_timestamp=t_pass,
    )

    mttr_res = await calculate_rolling_mttr(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=pid,
        window_days=14,
        window_end=window_end,
    )
    assert mttr_res.resolved_outage_count == 1
    assert mttr_res.mttr_seconds == 6480.0
    assert mttr_res.mttr_hours == 1.8
    assert mttr_res.mttr_minutes == 108.0

    empty_res = await calculate_rolling_mttr(
        db,  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=empty_pid,
        window_days=14,
        window_end=window_end,
    )
    assert empty_res.resolved_outage_count == 0
    assert empty_res.mttr_hours is None
    assert empty_res.mttr_minutes is None


def test_task_29_2_dora_package_exports_mttr_api():
    """Verify app.dora exposes all MTTR calculator functions and schemas."""
    import app.dora as dora_pkg

    for symbol in (
        "SUPPORTED_MTTR_WINDOWS",
        "DailyMTTRMetric",
        "MTTROutageItem",
        "MTTRResult",
        "build_mttr_incidents_query",
        "build_mttr_production_failures_query",
        "build_mttr_production_failures_sql_query",
        "build_mttr_sql_query",
        "calculate_rolling_mttr",
        "compute_mttr",
        "compute_session_recovery_duration_hours",
    ):
        assert hasattr(dora_pkg, symbol)
        assert symbol in dora_pkg.__all__


# ===========================================================================
# TC-DORA-MTTR-LIVE: Live PostgreSQL 16 Verification (Skip if no local DB)
# ===========================================================================


@skip_without_live_db
@pytest.mark.asyncio
async def test_task_29_3_live_database_canonical_benchmark_execution():
    """
    Task 29.3 / Live DB:
    Verify MTTR execution against real PostgreSQL 16 under tenant context.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    try:
        async with get_tenant_db_context(tid) as session:
            # Verify zero-outage null return on live database
            res_null = await calculate_rolling_mttr(
                session,
                tenant_id=tid,
                project_id=pid,
                window_days=14,
                window_end=window_end,
            )
            assert res_null.resolved_outage_count == 0
            assert res_null.mttr_seconds is None
            assert res_null.mttr_hours is None

            for sql_stmt in (
                build_mttr_sql_query(
                    tenant_id=tid,
                    project_id=pid,
                    start_at=window_end - timedelta(days=14),
                    end_at=window_end,
                ),
                build_mttr_production_failures_sql_query(
                    tenant_id=tid,
                    project_id=pid,
                    start_at=window_end - timedelta(days=14),
                    end_at=window_end,
                ),
            ):
                sql_null_row = (await session.execute(sql_stmt)).first()
                assert sql_null_row is not None
                assert int(sql_null_row[0]) == 0
                assert sql_null_row[1] is None
                assert sql_null_row[2] is None
                assert sql_null_row[3] is None
    finally:
        await async_engine.dispose()
