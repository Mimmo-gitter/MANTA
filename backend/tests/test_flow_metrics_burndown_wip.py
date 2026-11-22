"""
Task 76.7 / SCRUM-474: Pytest test suite for sprint burndown and WIP calculation accuracy.
Parent Story: SCRUM-370 (US-76: Sprint Burndown and WIP Flow Metrics).

Automated pytest verification for:
- GET /api/v1/projects/{project_id}/flow-metrics
- GET /api/v1/projects/{project_id}/sprints/burndown
- GET /api/v1/projects/{project_id}/sprints

Verifies all backend test scenarios from Task 76.5 specification:
- TC-SPRINT-FLOW-001: Sprint Baseline Initialization (40 SP Committed on Day 1)
- TC-SPRINT-FLOW-002: First Completion Batch (8 SP Done on Day 3 -> 32 SP Remaining)
- TC-SPRINT-FLOW-003: Second Completion Batch (12 SP Done on Day 6 -> 20 SP Remaining)
- TC-SPRINT-FLOW-004: Linear Ideal Burndown Slope Calculation & Contiguity
- TC-SPRINT-FLOW-005: WIP State Counting: Exactly 4 Issues in "In Progress"
- TC-SPRINT-FLOW-006: Status WIP Limit Congestion Alert (over_limit = True)
- TC-SPRINT-FLOW-007: Per-Developer WIP Limit Breach & Pseudonym Masking
- TC-SPRINT-FLOW-008: RBAC Enforcement: DEVELOPER, TOP_MANAGEMENT, SOFTWARE_ARCHITECT Forbidden (HTTP 403)
- TC-SPRINT-FLOW-009: RBAC Enforcement: SCRUM_MASTER, PROJECT_MANAGER, DELIVERY_DIRECTOR, TENANT_ADMIN Allowed (HTTP 200)
- TC-SPRINT-FLOW-010: Multi-Tenant Row-Level Security (RLS) Isolation (HTTP 404 Not Found)
- Edge cases: Empty sprint, missing dates, single-day sprint, fallback status/resolved_at, unassigned WIP.
"""

from __future__ import annotations

from collections import namedtuple
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import hashlib
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
import pytest

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
from app.db.models.models import (
    Project,
    Sprint,
)
from app.main import app
from app.projects.flow_metrics import (
    compute_burndown,
    compute_wip,
    compute_wip_by_developer,
    get_burndown_response,
    get_flow_metrics_response,
    list_project_sprints_response,
)

# ---------------------------------------------------------------------------
# Authoritative Fixture Constants & Identifiers (Task 76.5 §3)
# ---------------------------------------------------------------------------

TENANT_ALPHA_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_BETA_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

PROJECT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
SPRINT_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")

# Sprint Timeline: 2026-11-01T00:00:00Z to 2026-11-10T23:59:59Z (10 days)
SPRINT_START = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
SPRINT_END = datetime(2026, 11, 10, 23, 59, 59, tzinfo=timezone.utc)

# 10-Issue IDs
ISSUE_IDS = [uuid.uuid4() for _ in range(10)]

# Namedtuples for lightweight mock query rows
BurndownIssueRow = namedtuple("BurndownIssueRow", ["id", "story_points", "status", "resolved_at"])
TransitionRow = namedtuple(
    "TransitionRow", ["issue_id", "from_status", "to_status", "transitioned_at"]
)


def _build_authoritative_fixture():
    """
    Constructs the authoritative 10-issue / 40-SP sprint fixture
    matching Section 3.2 of the specification.
    """
    project = Project(
        id=PROJECT_ID,
        tenant_id=TENANT_ALPHA_ID,
        name="Core Payments Platform",
        slug="core-payments",
        wip_limit_per_dev=2,
    )

    sprint = Sprint(
        id=SPRINT_ID,
        tenant_id=TENANT_ALPHA_ID,
        project_id=PROJECT_ID,
        external_sprint_id="SPRINT-42",
        name="Sprint 42 (Manta Delivery)",
        state="active",
        start_date=SPRINT_START,
        end_date=SPRINT_END,
        goal="Deliver core payment gateway integrations and refactoring",
    )

    # 10 Issues: total 40.0 SP
    # MANTA-101 (4.0), MANTA-102 (4.0), MANTA-103 (5.0), MANTA-104 (7.0) -> Done
    # MANTA-105 (3.0), MANTA-106 (5.0), MANTA-107 (2.0), MANTA-108 (4.0) -> In Progress
    # MANTA-109 (3.0), MANTA-110 (3.0) -> To Do
    issues_manifest = [
        # (id, key, sp, pseudo, initial_status, current_status)
        (ISSUE_IDS[0], "MANTA-101", 4.0, "pseudo-dev-alpha", "To Do", "Done"),
        (ISSUE_IDS[1], "MANTA-102", 4.0, "pseudo-dev-beta", "To Do", "Done"),
        (ISSUE_IDS[2], "MANTA-103", 5.0, "pseudo-dev-gamma", "To Do", "Done"),
        (ISSUE_IDS[3], "MANTA-104", 7.0, "pseudo-dev-delta", "To Do", "Done"),
        (ISSUE_IDS[4], "MANTA-105", 3.0, "pseudo-dev-alpha", "To Do", "In Progress"),
        (ISSUE_IDS[5], "MANTA-106", 5.0, "pseudo-dev-alpha", "To Do", "In Progress"),
        (ISSUE_IDS[6], "MANTA-107", 2.0, "pseudo-dev-alpha", "To Do", "In Progress"),
        (ISSUE_IDS[7], "MANTA-108", 4.0, "pseudo-dev-beta", "To Do", "In Progress"),
        (ISSUE_IDS[8], "MANTA-109", 3.0, "pseudo-dev-gamma", "To Do", "To Do"),
        (ISSUE_IDS[9], "MANTA-110", 3.0, "pseudo-dev-delta", "To Do", "To Do"),
    ]

    issue_rows = [
        BurndownIssueRow(
            id=item[0],
            story_points=item[2],
            status=item[5],
            resolved_at=None,
        )
        for item in issues_manifest
    ]

    # Status Transitions
    transitions = [
        # Day 3 completions (8 SP):
        TransitionRow(
            issue_id=ISSUE_IDS[0],
            from_status="To Do",
            to_status="Done",
            transitioned_at=datetime(2026, 11, 3, 14, 0, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[1],
            from_status="To Do",
            to_status="Done",
            transitioned_at=datetime(2026, 11, 3, 16, 30, 0, tzinfo=timezone.utc),
        ),
        # Day 2 / Day 3 in progress transitions for MANTA-103 and 104:
        TransitionRow(
            issue_id=ISSUE_IDS[2],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[3],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 2, 11, 0, 0, tzinfo=timezone.utc),
        ),
        # Day 6 completions (12 SP):
        TransitionRow(
            issue_id=ISSUE_IDS[2],
            from_status="In Progress",
            to_status="Done",
            transitioned_at=datetime(2026, 11, 6, 11, 15, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[3],
            from_status="In Progress",
            to_status="Done",
            transitioned_at=datetime(2026, 11, 6, 17, 45, 0, tzinfo=timezone.utc),
        ),
        # Unfinished issues moving to In Progress:
        TransitionRow(
            issue_id=ISSUE_IDS[4],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[5],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 5, 10, 30, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[6],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 6, 14, 0, 0, tzinfo=timezone.utc),
        ),
        TransitionRow(
            issue_id=ISSUE_IDS[7],
            from_status="To Do",
            to_status="In Progress",
            transitioned_at=datetime(2026, 11, 6, 15, 0, 0, tzinfo=timezone.utc),
        ),
    ]

    # WIP status counts (terminal statuses excluded):
    # In Progress: 4, To Do: 2
    wip_status_counts = [("In Progress", 4), ("To Do", 2)]
    wip_limits = [("In Progress", 3)]

    # Developer WIP counts (unfinished issues only):
    # pseudo-dev-alpha: MANTA-105, 106, 107 (count = 3)
    # pseudo-dev-beta: MANTA-108 (count = 1)
    # pseudo-dev-gamma: MANTA-109 (count = 1)
    # pseudo-dev-delta: MANTA-110 (count = 1)
    wip_dev_counts = [
        ("pseudo-dev-alpha", 3),
        ("pseudo-dev-beta", 1),
        ("pseudo-dev-gamma", 1),
        ("pseudo-dev-delta", 1),
    ]

    return {
        "project": project,
        "sprint": sprint,
        "issue_rows": issue_rows,
        "transitions": transitions,
        "wip_status_counts": wip_status_counts,
        "wip_limits": wip_limits,
        "wip_dev_counts": wip_dev_counts,
    }


# ---------------------------------------------------------------------------
# Mock Query & AsyncSession Wrapper
# ---------------------------------------------------------------------------


class _MockScalarResult:
    """Mock query result supporting all SQLAlchemy result inspection methods."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _MockScalarResult:
        unpacked = []
        for item in self._items:
            if isinstance(item, (list, tuple)) and len(item) == 1:
                unpacked.append(item[0])
            else:
                unpacked.append(item)
        return _MockScalarResult(unpacked)

    def all(self) -> list[Any]:
        return self._items

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one_or_none(self) -> Any:
        if not self._items:
            return None
        item = self._items[0]
        if isinstance(item, (list, tuple)) and len(item) == 1:
            return item[0]
        return item

    def scalar_one(self) -> Any:
        if not self._items:
            raise ValueError("No rows returned from scalar_one")
        item = self._items[0]
        if isinstance(item, (list, tuple)) and len(item) == 1:
            return item[0]
        return item


class _MockFlowMetricsSession:
    """
    Mock AsyncSession providing the 10-issue / 40-SP sprint fixture
    under strict multi-tenant isolation.
    """

    def __init__(
        self,
        tenant_id: uuid.UUID,
        fixture: dict[str, Any],
        *,
        cross_tenant_override: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.fixture = fixture
        self.cross_tenant_override = cross_tenant_override

    async def execute(self, stmt: Any) -> _MockScalarResult:
        stmt_str = str(stmt).lower()

        # Multi-tenant RLS check: If tenant doesn't match and not overridden, return empty
        if self.tenant_id != TENANT_ALPHA_ID and not self.cross_tenant_override:
            return _MockScalarResult([])

        params = {}
        try:
            params = stmt.compile().params
        except Exception:
            pass

        # 1. Project WIP limit per developer
        if "projects.wip_limit_per_dev" in stmt_str:
            return _MockScalarResult([self.fixture["project"].wip_limit_per_dev])

        # 2. Project existence check
        if "projects" in stmt_str and "projects.id" in stmt_str:
            for k, v in params.items():
                if ("id" in k or "project" in k) and "tenant" not in k and isinstance(v, uuid.UUID):
                    if v != self.fixture["project"].id:
                        return _MockScalarResult([])
            return _MockScalarResult([(self.fixture["project"].id,)])

        # 3. Sprints listing or find_sprint
        if "sprints" in stmt_str:
            for k, v in params.items():
                if (
                    "id" in k
                    and "tenant" not in k
                    and "project" not in k
                    and isinstance(v, uuid.UUID)
                ):
                    if v != self.fixture["sprint"].id:
                        return _MockScalarResult([])
                if "project" in k and isinstance(v, uuid.UUID):
                    if v != self.fixture["project"].id:
                        return _MockScalarResult([])
            return _MockScalarResult([self.fixture["sprint"]])

        # 4. Status transitions for issues
        if "issue_transitions" in stmt_str:
            return _MockScalarResult(self.fixture["transitions"])

        # 5. Developer WIP grouping by pseudonym
        if "assignee_pseudonym_id" in stmt_str:
            return _MockScalarResult(self.fixture["wip_dev_counts"])

        # 6. WIP limits configuration
        if "wip_limits" in stmt_str:
            return _MockScalarResult(self.fixture["wip_limits"])

        # 7. WIP status counting (Issue.status + count)
        if "issues.status" in stmt_str and "count" in stmt_str:
            return _MockScalarResult(self.fixture["wip_status_counts"])

        # 8. Burndown issues query (id, story_points, status, resolved_at)
        if "issues.story_points" in stmt_str:
            return _MockScalarResult(self.fixture["issue_rows"])

        return _MockScalarResult([])


# ---------------------------------------------------------------------------
# JWT Auth & Test Setup Fixtures
# ---------------------------------------------------------------------------


def generate_rsa_keypair(kid: str = "flow-test-key") -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


def make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "flow-test-key",
    sub: str = "test_scrum_master",
    roles: list[str] | None = None,
    tenant_id: uuid.UUID = TENANT_ALPHA_ID,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "name": f"User {sub}",
        "email": f"{sub}@manta.local",
        "preferred_username": sub,
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
    }
    if roles is not None:
        payload["realm_access"] = {"roles": roles}
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


@pytest.fixture
def auth_fixture():
    private_key, jwk = generate_rsa_keypair("flow-test-key")

    async def mock_handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_handle_request),
    )
    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {
        "private_key": private_key,
        "jwk": jwk,
        "client": client,
    }

    app.state.jwks_client = original_client


@pytest.fixture
def flow_fixture():
    return _build_authoritative_fixture()


@pytest.fixture
def mock_db_context(flow_fixture):
    @asynccontextmanager
    async def _mock_context(tenant_id: Any) -> AsyncGenerator[_MockFlowMetricsSession, None]:
        tid = uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id
        yield _MockFlowMetricsSession(tid, flow_fixture)

    with mock.patch("app.projects.flow_metrics.get_tenant_db_context", _mock_context):
        yield _mock_context


# ---------------------------------------------------------------------------
# Test Suite 1: Calculation Engine Scenarios (TC-001 through TC-004)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_sprint_flow_001_sprint_baseline_initialization_day_1(flow_fixture):
    """
    TC-SPRINT-FLOW-001: Sprint Baseline Initialization (40 SP Committed on Day 1).
    Day 1 remaining points equal 40.0 SP, ideal slope starts at 40.0 SP.
    Traces to US-76 AC-1 and Invariant 1.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    sprint = flow_fixture["sprint"]

    # Compute burndown directly at Day 1 (cutoff = Day 1 start)
    series = await compute_burndown(db, TENANT_ALPHA_ID, sprint, today=date(2026, 11, 10))

    assert len(series) == 10
    day_1 = series[0]
    assert day_1["date"] == "2026-11-01"
    assert day_1["remaining_sp"] == 40.0
    assert day_1["ideal_sp"] == 40.0


@pytest.mark.asyncio
async def test_tc_sprint_flow_002_first_completion_batch_day_3(flow_fixture):
    """
    TC-SPRINT-FLOW-002: First Completion Batch (8 SP Done on Day 3 -> 32 SP Remaining).
    MANTA-101 (4 SP) and MANTA-102 (4 SP) transition to Done on Day 3.
    Day 3 remaining points drop to 32.0 SP; Day 2 remains 40.0 SP.
    Traces to US-76 AC-1 and Invariant 1.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    sprint = flow_fixture["sprint"]

    series = await compute_burndown(db, TENANT_ALPHA_ID, sprint, today=date(2026, 11, 10))

    # Day 2 remains 40.0 SP
    assert series[1]["date"] == "2026-11-02"
    assert series[1]["remaining_sp"] == 40.0
    assert series[1]["ideal_sp"] == 35.56

    # Day 3 drops to 32.0 SP
    day_3 = series[2]
    assert day_3["date"] == "2026-11-03"
    assert day_3["remaining_sp"] == 32.0
    assert day_3["ideal_sp"] == 31.11


@pytest.mark.asyncio
async def test_tc_sprint_flow_003_second_completion_batch_day_6(flow_fixture):
    """
    TC-SPRINT-FLOW-003: Second Completion Batch (12 SP Done on Day 6 -> 20 SP Remaining).
    MANTA-103 (5 SP) and MANTA-104 (7 SP) transition to Done on Day 6.
    Cumulative Done = 20.0 SP. Day 6 drops to 20.0 SP and plateau holds through Day 10.
    Traces to US-76 AC-1 and Invariant 1.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    sprint = flow_fixture["sprint"]

    series = await compute_burndown(db, TENANT_ALPHA_ID, sprint, today=date(2026, 11, 10))

    # Days 4 and 5 plateau at 32.0 SP
    assert series[3]["date"] == "2026-11-04"
    assert series[3]["remaining_sp"] == 32.0
    assert series[4]["date"] == "2026-11-05"
    assert series[4]["remaining_sp"] == 32.0

    # Day 6 drops by 12 SP to 20.0 SP
    day_6 = series[5]
    assert day_6["date"] == "2026-11-06"
    assert day_6["remaining_sp"] == 20.0
    assert day_6["ideal_sp"] == 17.78

    # Days 7 through 10 remain flat at 20.0 SP
    for day_idx in range(6, 10):
        assert series[day_idx]["remaining_sp"] == 20.0, (
            f"Day {day_idx + 1} remaining SP mismatch: {series[day_idx]}"
        )


@pytest.mark.asyncio
async def test_tc_sprint_flow_004_linear_ideal_burndown_slope_and_monotonicity(flow_fixture):
    """
    TC-SPRINT-FLOW-004: Linear Ideal Burndown Slope Calculation & Contiguity.
    Ideal trajectory strictly equals [40.0, 35.56, 31.11, 26.67, 22.22, 17.78, 13.33, 8.89, 4.44, 0.0].
    Strictly monotonically non-increasing across all 10 calendar days.
    Traces to US-76 AC-1 and Invariant 2.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    sprint = flow_fixture["sprint"]

    series = await compute_burndown(db, TENANT_ALPHA_ID, sprint, today=date(2026, 11, 10))

    expected_ideals = [40.0, 35.56, 31.11, 26.67, 22.22, 17.78, 13.33, 8.89, 4.44, 0.0]
    assert len(series) == len(expected_ideals)

    for idx, point in enumerate(series):
        assert point["ideal_sp"] == expected_ideals[idx], (
            f"Index {idx} ideal_sp expected {expected_ideals[idx]} but got {point['ideal_sp']}"
        )
        if idx > 0:
            assert point["ideal_sp"] < series[idx - 1]["ideal_sp"], (
                f"Ideal slope violation at index {idx}: {point['ideal_sp']} >= {series[idx - 1]['ideal_sp']}"
            )


# ---------------------------------------------------------------------------
# Test Suite 2: WIP Aggregation & Congestion Scenarios (TC-005 through TC-007)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tc_sprint_flow_005_wip_state_counting_terminal_status_exclusion(flow_fixture):
    """
    TC-SPRINT-FLOW-005: WIP State Counting: Exactly 4 Issues in 'In Progress'.
    Unfinished issues aggregated by status; terminal statuses (Done, Closed) strictly excluded.
    Traces to US-76 AC-2 and Invariant 3.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    wip = await compute_wip(db, TENANT_ALPHA_ID, PROJECT_ID, flow_fixture["sprint"])

    wip_map = {item["status"]: item["count"] for item in wip}
    assert wip_map.get("In Progress") == 4
    assert wip_map.get("To Do") == 2

    # Assert terminal statuses are not present
    for terminal in ("Done", "Closed", "Resolved", "Complete", "Completed"):
        assert terminal not in wip_map


@pytest.mark.asyncio
async def test_tc_sprint_flow_006_status_wip_limit_congestion_alert(flow_fixture):
    """
    TC-SPRINT-FLOW-006: Status WIP Limit Congestion Alert (over_limit = True).
    'In Progress' count (4) > limit (3) triggers over_limit = True.
    'To Do' count (2) with no configured limit has over_limit = False.
    Traces to US-76 AC-2 and Invariant 3.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    wip = await compute_wip(db, TENANT_ALPHA_ID, PROJECT_ID, flow_fixture["sprint"])

    in_progress = next(item for item in wip if item["status"] == "In Progress")
    assert in_progress["count"] == 4
    assert in_progress["limit"] == 3
    assert in_progress["over_limit"] is True

    to_do = next(item for item in wip if item["status"] == "To Do")
    assert to_do["count"] == 2
    assert to_do["limit"] is None
    assert to_do["over_limit"] is False


@pytest.mark.asyncio
async def test_tc_sprint_flow_007_per_developer_wip_limit_and_pseudonym_masking(flow_fixture):
    """
    TC-SPRINT-FLOW-007: Per-Developer WIP Limit Breach & Pseudonym Masking.
    Developer Alpha holds 3 WIP issues > limit 2 -> over_limit = True.
    Pseudonym masked as 'Developer <HEX>'; raw pseudonym never exposed.
    Traces to US-76 AC-2 and Invariant 4.
    """
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    dev_wip = await compute_wip_by_developer(
        db, TENANT_ALPHA_ID, PROJECT_ID, flow_fixture["sprint"]
    )

    assert dev_wip["limit_per_developer"] == 2
    assert dev_wip["unassigned_count"] == 0

    developers = dev_wip["developers"]
    assert len(developers) == 4

    # Developer Alpha check (highest WIP = 3)
    dev_alpha = developers[0]
    expected_alpha_hex = hashlib.sha256("pseudo-dev-alpha".encode("utf-8")).hexdigest()[:10].upper()
    assert dev_alpha["developer_label"] == f"Developer {expected_alpha_hex}"
    assert dev_alpha["count"] == 3
    assert dev_alpha["limit"] == 2
    assert dev_alpha["over_limit"] is True

    # Other developers (count = 1 <= limit 2)
    for dev in developers[1:]:
        assert dev["count"] == 1
        assert dev["limit"] == 2
        assert dev["over_limit"] is False

    # Privacy guarantee: raw pseudonym strings must NOT be exposed
    serialized = json.dumps(dev_wip)
    assert "pseudo-dev-alpha" not in serialized
    assert "pseudo-dev-beta" not in serialized
    assert "pseudo-dev-gamma" not in serialized
    assert "pseudo-dev-delta" not in serialized


# ---------------------------------------------------------------------------
# Test Suite 3: RBAC & Access Control Matrix (TC-008 through TC-010)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("denied_role", ["DEVELOPER", "TOP_MANAGEMENT", "SOFTWARE_ARCHITECT"])
async def test_tc_sprint_flow_008_rbac_denied_roles_http_403(
    auth_fixture, mock_db_context, denied_role
):
    """
    TC-SPRINT-FLOW-008: RBAC Enforcement: DEVELOPER, TOP_MANAGEMENT, SOFTWARE_ARCHITECT Forbidden (HTTP 403).
    Endpoint requires Permission.VIEW_SPRINT_BURNDOWN_WIP.
    Traces to US-76 AC-3 and Invariant 5.
    """
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=[denied_role], sub=f"test_{denied_role.lower()}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Flow metrics endpoint
        resp_flow = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/flow-metrics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_flow.status_code == 403
        assert resp_flow.json() == FORBIDDEN_RESPONSE

        # 2. Sprint burndown endpoint
        resp_burndown = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/sprints/burndown",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_burndown.status_code == 403
        assert resp_burndown.json() == FORBIDDEN_RESPONSE

        # 3. List sprints endpoint
        resp_sprints = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/sprints",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_sprints.status_code == 403
        assert resp_sprints.json() == FORBIDDEN_RESPONSE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed_role",
    ["SCRUM_MASTER", "PROJECT_MANAGER", "DELIVERY_DIRECTOR", "TENANT_ADMIN"],
)
async def test_tc_sprint_flow_009_rbac_allowed_roles_http_200(
    auth_fixture, mock_db_context, allowed_role
):
    """
    TC-SPRINT-FLOW-009: RBAC Enforcement: SCRUM_MASTER & Management Allowed (HTTP 200).
    Granted to SCRUM_MASTER, PROJECT_MANAGER, DELIVERY_DIRECTOR, TENANT_ADMIN.
    Traces to US-76 AC-3 and Invariant 5.
    """
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=[allowed_role], sub=f"test_{allowed_role.lower()}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. GET /flow-metrics
        resp = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/flow-metrics?sprint_id={SPRINT_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, f"Role {allowed_role} failed: {resp.text}"
        data = resp.json()

        assert "burndown" in data
        assert "wip" in data
        assert "wip_by_developer" in data
        assert data["sprint"]["name"] == "Sprint 42 (Manta Delivery)"
        assert len(data["burndown"]) == 10
        assert data["burndown"][0]["remaining_sp"] == 40.0
        assert data["burndown"][2]["remaining_sp"] == 32.0
        assert data["burndown"][5]["remaining_sp"] == 20.0

        # 2. GET /sprints/burndown
        resp_bd = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/sprints/burndown?sprint_id={SPRINT_ID}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_bd.status_code == 200
        bd_data = resp_bd.json()
        assert bd_data["sprint_name"] == "Sprint 42 (Manta Delivery)"
        assert len(bd_data["series"]) == 10


@pytest.mark.asyncio
async def test_tc_sprint_flow_010_cross_tenant_rls_isolation_http_404(
    auth_fixture, mock_db_context
):
    """
    TC-SPRINT-FLOW-010: Multi-Tenant Row-Level Security (RLS) Isolation.
    Tenant Beta accessing Tenant Alpha project returns HTTP 404 Not Found (anti-enumeration).
    Traces to US-76 AC-3 and Invariant 5.
    """
    pk = auth_fixture["private_key"]
    # Token minted for Tenant Beta with SCRUM_MASTER role
    beta_token = make_token(
        pk,
        roles=["SCRUM_MASTER"],
        sub="beta_scrum_master",
        tenant_id=TENANT_BETA_ID,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/flow-metrics",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Project not found"

        # Also verify /sprints/burndown cross-tenant isolation
        resp_bd = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/sprints/burndown",
            headers={"Authorization": f"Bearer {beta_token}"},
        )
        assert resp_bd.status_code == 404
        assert resp_bd.json()["detail"] == "Project not found"


# ---------------------------------------------------------------------------
# Test Suite 4: Edge Cases & Resiliency Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edge_case_empty_sprint_zero_issues(flow_fixture):
    """A sprint with no committed issues returns an empty series [] and zero WIP."""
    empty_fixture = dict(flow_fixture)
    empty_fixture["issue_rows"] = []
    empty_fixture["transitions"] = []
    empty_fixture["wip_status_counts"] = []
    empty_fixture["wip_limits"] = []
    empty_fixture["wip_dev_counts"] = []

    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, empty_fixture)
    series = await compute_burndown(db, TENANT_ALPHA_ID, flow_fixture["sprint"])
    assert series == []

    wip = await compute_wip(db, TENANT_ALPHA_ID, PROJECT_ID, flow_fixture["sprint"])
    assert wip == []


@pytest.mark.asyncio
async def test_edge_case_sprint_missing_dates(flow_fixture):
    """A sprint missing start_date or end_date gracefully returns an empty series."""
    sprint_no_dates = Sprint(
        id=uuid.uuid4(),
        tenant_id=TENANT_ALPHA_ID,
        project_id=PROJECT_ID,
        name="Sprint No Dates",
        start_date=None,
        end_date=None,
    )
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    series = await compute_burndown(db, TENANT_ALPHA_ID, sprint_no_dates)
    assert series == []


@pytest.mark.asyncio
async def test_edge_case_single_day_sprint(flow_fixture):
    """A single-day sprint (N=1) calculates ideal_sp = total_sp without ZeroDivisionError."""
    single_day_sprint = Sprint(
        id=uuid.uuid4(),
        tenant_id=TENANT_ALPHA_ID,
        project_id=PROJECT_ID,
        name="Single Day Sprint",
        start_date=datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 11, 1, 23, 59, 59, tzinfo=timezone.utc),
    )
    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, flow_fixture)
    series = await compute_burndown(db, TENANT_ALPHA_ID, single_day_sprint, today=date(2026, 11, 1))

    assert len(series) == 1
    assert series[0]["ideal_sp"] == 40.0
    assert series[0]["remaining_sp"] == 40.0


@pytest.mark.asyncio
async def test_edge_case_fallback_to_resolved_at(flow_fixture):
    """Issues without transition history accurately fall back to resolved_at timestamp."""
    resolved_issue_id = uuid.uuid4()
    fallback_rows = [
        BurndownIssueRow(
            id=resolved_issue_id,
            story_points=10.0,
            status="Done",
            resolved_at=datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc),
        )
    ]
    custom_fixture = dict(flow_fixture)
    custom_fixture["issue_rows"] = fallback_rows
    custom_fixture["transitions"] = []  # No transitions

    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, custom_fixture)
    series = await compute_burndown(
        db, TENANT_ALPHA_ID, flow_fixture["sprint"], today=date(2026, 11, 10)
    )

    # Day 1 & Day 2: remaining = 10.0 SP
    assert series[0]["remaining_sp"] == 10.0
    assert series[1]["remaining_sp"] == 10.0
    # Day 3: resolved_at < cutoff -> resolved -> remaining = 0.0 SP
    assert series[2]["remaining_sp"] == 0.0


@pytest.mark.asyncio
async def test_edge_case_fallback_to_issue_status(flow_fixture):
    """Issues without transitions or resolved_at fall back to current status."""
    done_issue = BurndownIssueRow(
        id=uuid.uuid4(),
        story_points=8.0,
        status="Done",
        resolved_at=None,
    )
    custom_fixture = dict(flow_fixture)
    custom_fixture["issue_rows"] = [done_issue]
    custom_fixture["transitions"] = []

    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, custom_fixture)
    series = await compute_burndown(
        db, TENANT_ALPHA_ID, flow_fixture["sprint"], today=date(2026, 11, 10)
    )

    # Without transitions or resolved_at, is_done evaluates to True for all days
    assert series[0]["remaining_sp"] == 0.0
    assert series[0]["ideal_sp"] == 8.0


@pytest.mark.asyncio
async def test_edge_case_unassigned_wip_issues(flow_fixture):
    """Unassigned active issues increment unassigned_count in developer breakdown."""
    custom_fixture = dict(flow_fixture)
    custom_fixture["wip_dev_counts"] = [
        ("pseudo-dev-alpha", 2),
        (None, 3),  # 3 unassigned issues
    ]

    db = _MockFlowMetricsSession(TENANT_ALPHA_ID, custom_fixture)
    dev_wip = await compute_wip_by_developer(
        db, TENANT_ALPHA_ID, PROJECT_ID, flow_fixture["sprint"]
    )

    assert dev_wip["unassigned_count"] == 3
    assert len(dev_wip["developers"]) == 1
    assert dev_wip["developers"][0]["count"] == 2
    assert dev_wip["developers"][0]["over_limit"] is False


@pytest.mark.asyncio
async def test_edge_case_nonexistent_project_and_sprint_404(auth_fixture, mock_db_context):
    """Requesting non-existent project or sprint returns HTTP 404 Not Found."""
    pk = auth_fixture["private_key"]
    token = make_token(pk, roles=["SCRUM_MASTER"])
    random_project_id = uuid.uuid4()
    random_sprint_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Non-existent project
        resp = await client.get(
            f"/api/v1/projects/{random_project_id}/flow-metrics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Project not found"

        # Non-existent sprint on burndown endpoint
        resp_bd = await client.get(
            f"/api/v1/projects/{PROJECT_ID}/sprints/burndown?sprint_id={random_sprint_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp_bd.status_code == 404
        assert resp_bd.json()["detail"] == "Sprint not found"


# ---------------------------------------------------------------------------
# Test Suite 5: Direct Service Functions Verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_service_get_flow_metrics_response(flow_fixture, mock_db_context):
    """Directly verifies get_flow_metrics_response service function."""
    result = await get_flow_metrics_response(TENANT_ALPHA_ID, PROJECT_ID, SPRINT_ID)

    assert result["sprint"]["name"] == "Sprint 42 (Manta Delivery)"
    assert len(result["burndown"]) == 10
    assert result["burndown"][0]["remaining_sp"] == 40.0
    assert result["burndown"][2]["remaining_sp"] == 32.0
    assert result["burndown"][5]["remaining_sp"] == 20.0
    assert result["wip"][0]["status"] == "In Progress"
    assert result["wip"][0]["over_limit"] is True
    assert result["wip_by_developer"]["limit_per_developer"] == 2


@pytest.mark.asyncio
async def test_direct_service_get_burndown_response(flow_fixture, mock_db_context):
    """Directly verifies get_burndown_response service function."""
    result = await get_burndown_response(TENANT_ALPHA_ID, PROJECT_ID, SPRINT_ID)

    assert result["sprint_id"] == str(SPRINT_ID)
    assert result["sprint_name"] == "Sprint 42 (Manta Delivery)"
    assert len(result["series"]) == 10
    assert result["series"][0]["remaining_sp"] == 40.0
    assert result["series"][2]["remaining_sp"] == 32.0
    assert result["series"][5]["remaining_sp"] == 20.0


@pytest.mark.asyncio
async def test_direct_service_list_project_sprints_response(flow_fixture, mock_db_context):
    """Directly verifies list_project_sprints_response service function."""
    sprints = await list_project_sprints_response(TENANT_ALPHA_ID, PROJECT_ID)

    assert len(sprints) == 1
    assert sprints[0]["id"] == str(SPRINT_ID)
    assert sprints[0]["name"] == "Sprint 42 (Manta Delivery)"
    assert sprints[0]["state"] == "active"
