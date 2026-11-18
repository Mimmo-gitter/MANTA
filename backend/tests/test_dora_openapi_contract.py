"""
US-30 / Task 30.1: OpenAPI 3.1 Pydantic contract tests for GET /api/v1/projects/{id}/dora-metrics
and GET /api/v1/dora/projects/{project_id}.

Verifies:
- OpenAPI 3.1.0 (JSON Schema 2020-12) nullable representation (`anyOf: [number, null]`).
- Required presence of all aggregate and daily trend metric keys (never omitted when null).
- Explicit distinction between `null` (0 qualifying events) and `0.0` (e.g., 0.0% CFR).
- Enum constraints for `window` (`14d` | `30d`), `timeframe_days` (`14` | `30`), and
  `authoritative_deployment_signal`.
- Guardrails against negative metrics, CFR > 100%, NaN/Infinity, and duplicate trend dates.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import json
import time
from typing import Any
import unittest.mock as mock
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.openapi.utils import get_openapi
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
import pytest

from app.auth.jwks import JWKSClient
from app.auth.permissions import FORBIDDEN_RESPONSE
from app.config import settings
from app.db.models.models import Deployment, ProductionFailure, Project, PullRequest
from app.deployments.schemas import DeploymentOutcome, DeploymentSignalSource
from app.dora.schemas import (
    DoraAggregates,
    DoraDailyPoint,
    DoraMetricsResponse,
    DoraTimeframeDays,
)
from app.dora.service import (
    assemble_dora_metrics_response,
    calculate_project_dora_metrics,
    compute_change_failure_rate,
    compute_deployment_frequency,
    compute_lead_time_metrics,
    compute_mttr,
)
from app.main import app


def _assert_nullable_number_schema(
    prop_schema: dict,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> None:
    """Assert OpenAPI 3.1 / JSON Schema 2020-12 nullable number schema."""
    assert "anyOf" in prop_schema, f"Expected OpenAPI 3.1 'anyOf' nullable schema: {prop_schema}"
    variants = prop_schema["anyOf"]
    assert {"type": "null"} in variants, f"Missing explicit null branch in {variants}"

    number_variants = [v for v in variants if v.get("type") == "number"]
    assert len(number_variants) == 1, f"Expected single number variant in {variants}"
    num_schema = number_variants[0]
    assert num_schema.get("minimum") == minimum
    if maximum is not None:
        assert num_schema.get("maximum") == maximum


def test_dora_aggregates_openapi_31_nullable_schema() -> None:
    """All 4 aggregate metrics must be required and explicitly nullable in OpenAPI 3.1."""
    schema = DoraMetricsResponse.model_json_schema()
    defs = schema["$defs"]
    agg_schema = defs["DoraAggregates"]

    expected_fields = [
        "lead_time_hours",
        "deployment_frequency_per_day",
        "change_failure_rate_pct",
        "mttr_hours",
    ]
    assert set(agg_schema["required"]) == set(expected_fields)

    props = agg_schema["properties"]
    _assert_nullable_number_schema(props["lead_time_hours"], minimum=0.0)
    _assert_nullable_number_schema(props["deployment_frequency_per_day"], minimum=0.0)
    _assert_nullable_number_schema(props["change_failure_rate_pct"], minimum=0.0, maximum=100.0)
    _assert_nullable_number_schema(props["mttr_hours"], minimum=0.0)


def test_dora_daily_point_openapi_31_nullable_schema() -> None:
    """Each DoraDailyPoint must require UTC date and all 4 explicitly nullable metrics."""
    schema = DoraMetricsResponse.model_json_schema()
    point_schema = schema["$defs"]["DoraDailyPoint"]

    expected_fields = {
        "date",
        "lead_time_hours",
        "deployment_frequency_per_day",
        "change_failure_rate_pct",
        "mttr_hours",
    }
    assert set(point_schema["required"]) == expected_fields

    props = point_schema["properties"]
    assert props["date"]["type"] == "string"
    assert props["date"]["format"] == "date"

    _assert_nullable_number_schema(props["lead_time_hours"], minimum=0.0)
    _assert_nullable_number_schema(props["deployment_frequency_per_day"], minimum=0.0)
    _assert_nullable_number_schema(props["change_failure_rate_pct"], minimum=0.0, maximum=100.0)
    _assert_nullable_number_schema(props["mttr_hours"], minimum=0.0)


def test_dora_response_enums_and_top_level_fields() -> None:
    """DoraMetricsResponse must define project_id, window, signal, aggregates, notes, trend."""
    schema = DoraMetricsResponse.model_json_schema()
    props = schema["properties"]

    assert {
        "project_id",
        "window",
        "authoritative_deployment_signal",
        "aggregates",
        "notes",
        "trend",
    }.issubset(props.keys())

    assert props["project_id"]["type"] == "string"
    assert props["project_id"]["format"] == "uuid"
    assert props["window"]["type"] == "string"
    assert props["window"]["enum"] == ["14d", "30d"]
    assert props["notes"]["type"] == "array"
    assert props["notes"]["items"] == {"type": "string"}
    assert props["trend"]["type"] == "array"
    assert props["trend"]["items"] == {"$ref": "#/$defs/DoraDailyPoint"}

    signal_def = schema["$defs"]["DeploymentSignalSource"]
    assert signal_def["type"] == "string"
    assert signal_def["enum"] == [
        "github_deployments_api",
        "argocd_postsync_webhook",
        "direct_deployment_api",
    ]


def test_fastapi_openapi_31_contract_generation_for_both_routes() -> None:
    """
    FastAPI must emit a valid OpenAPI 3.1.0 document for both
    GET /api/v1/projects/{project_id}/dora-metrics and GET /api/v1/dora/projects/{project_id}.
    """
    contract_app = FastAPI(title="DORA Contract Test", version="3.0.0")
    projects_contract_router = APIRouter(prefix="/api/v1/projects")
    dora_contract_router = APIRouter(prefix="/api/v1/dora")

    @projects_contract_router.get("/{project_id}/dora-metrics", response_model=DoraMetricsResponse)
    async def get_project_dora_metrics(
        project_id: uuid.UUID,
        timeframe_days: DoraTimeframeDays = Query(default=30),
    ) -> DoraMetricsResponse:
        raise NotImplementedError

    @dora_contract_router.get("/projects/{project_id}", response_model=DoraMetricsResponse)
    async def get_dora_project_metrics(
        project_id: uuid.UUID,
        timeframe_days: DoraTimeframeDays = Query(default=30),
    ) -> DoraMetricsResponse:
        raise NotImplementedError

    contract_app.include_router(projects_contract_router)
    contract_app.include_router(dora_contract_router)

    openapi_doc = get_openapi(
        title=contract_app.title,
        version=contract_app.version,
        openapi_version="3.1.0",
        routes=contract_app.routes,
    )

    assert openapi_doc["openapi"] == "3.1.0"
    paths = openapi_doc["paths"]
    assert "/api/v1/projects/{project_id}/dora-metrics" in paths
    assert "/api/v1/dora/projects/{project_id}" in paths

    for route_path in (
        "/api/v1/projects/{project_id}/dora-metrics",
        "/api/v1/dora/projects/{project_id}",
    ):
        op = paths[route_path]["get"]
        params = {p["name"]: p for p in op["parameters"]}
        assert params["project_id"]["in"] == "path"
        assert params["project_id"]["schema"]["format"] == "uuid"
        assert params["timeframe_days"]["in"] == "query"
        assert params["timeframe_days"]["schema"]["enum"] == [14, 30]
        assert params["timeframe_days"]["schema"]["default"] == 30

        resp_200 = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert resp_200 == {"$ref": "#/components/schemas/DoraMetricsResponse"}

    comp_schemas = openapi_doc["components"]["schemas"]
    assert "DoraMetricsResponse" in comp_schemas
    assert "DoraAggregates" in comp_schemas
    assert "DoraDailyPoint" in comp_schemas
    _assert_nullable_number_schema(
        comp_schemas["DoraAggregates"]["properties"]["change_failure_rate_pct"],
        minimum=0.0,
        maximum=100.0,
    )


def test_null_vs_zero_point_zero_distinction_and_roundtrip() -> None:
    """
    Acceptance Criteria:
    JSON payload explicitly distinguishes `null` (0 events -> undefined)
    from `0.0` (e.g., 0.0% CFR when deployments succeeded with 0 failures).
    """
    project_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    start_day = date(2026, 11, 1)

    trend = [
        DoraDailyPoint(
            date=start_day + timedelta(days=i),
            lead_time_hours=23.5 if i == 0 else None,
            deployment_frequency_per_day=2.0 if i == 0 else 0.0,
            change_failure_rate_pct=0.0 if i == 0 else None,
            mttr_hours=None,
        )
        for i in range(14)
    ]

    payload = DoraMetricsResponse(
        project_id=project_id,
        window="14d",
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        aggregates=DoraAggregates(
            lead_time_hours=23.5,
            deployment_frequency_per_day=0.43,
            change_failure_rate_pct=0.0,
            mttr_hours=None,
        ),
        notes=[
            "mttr_hours is null: 0 resolved production health failure sessions in window.",
            "change_failure_rate_pct is 0.0%: 6 production deployments succeeded with 0 failures.",
        ],
        trend=trend,
    )

    raw = json.loads(payload.model_dump_json())
    assert raw["project_id"] == str(project_id)
    assert raw["window"] == "14d"
    assert raw["authoritative_deployment_signal"] == "github_deployments_api"

    # Explicit 0.0 vs null distinction at aggregate level
    assert raw["aggregates"]["change_failure_rate_pct"] == 0.0
    assert raw["aggregates"]["change_failure_rate_pct"] is not None
    assert raw["aggregates"]["mttr_hours"] is None
    assert "mttr_hours" in raw["aggregates"]

    # Explicit 0.0 vs null distinction at daily trend level
    assert len(raw["trend"]) == 14
    assert raw["trend"][0]["change_failure_rate_pct"] == 0.0
    assert raw["trend"][0]["mttr_hours"] is None
    assert raw["trend"][1]["lead_time_hours"] is None
    assert raw["trend"][1]["change_failure_rate_pct"] is None
    assert raw["trend"][1]["deployment_frequency_per_day"] == 0.0

    # Round-trip validation preserves exact null vs 0.0 distinction
    reparsed = DoraMetricsResponse.model_validate_json(payload.model_dump_json())
    assert reparsed == payload


def test_all_null_zero_event_project_serialization() -> None:
    """When 0 events exist, all 4 nullable aggregates serialize as null (never omitted or 0.0)."""
    payload = DoraMetricsResponse(
        project_id=uuid.uuid4(),
        window="30d",
        authoritative_deployment_signal=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK,
        aggregates=DoraAggregates(
            lead_time_hours=None,
            deployment_frequency_per_day=None,
            change_failure_rate_pct=None,
            mttr_hours=None,
        ),
        trend=[],
    )

    raw = json.loads(payload.model_dump_json())
    assert raw["notes"] == []
    assert raw["aggregates"] == {
        "lead_time_hours": None,
        "deployment_frequency_per_day": None,
        "change_failure_rate_pct": None,
        "mttr_hours": None,
    }


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"lead_time_hours": -0.01},
        {"deployment_frequency_per_day": -1.0},
        {"change_failure_rate_pct": -0.1},
        {"change_failure_rate_pct": 100.01},
        {"mttr_hours": -5.0},
        {"lead_time_hours": float("nan")},
        {"mttr_hours": float("inf")},
    ],
)
def test_aggregates_reject_out_of_range_and_non_finite_values(bad_kwargs: dict) -> None:
    """Negative values, CFR > 100%, NaN, and Infinity must fail validation."""
    base = {
        "lead_time_hours": 1.0,
        "deployment_frequency_per_day": 1.0,
        "change_failure_rate_pct": 0.0,
        "mttr_hours": None,
    }
    base.update(bad_kwargs)
    with pytest.raises(ValidationError):
        DoraAggregates(**base)


def test_response_rejects_invalid_window_signal_and_duplicate_dates() -> None:
    """Invalid windows, legacy/unknown signals, and duplicate trend dates must fail validation."""
    valid_aggs = DoraAggregates(
        lead_time_hours=None,
        deployment_frequency_per_day=None,
        change_failure_rate_pct=None,
        mttr_hours=None,
    )

    with pytest.raises(ValidationError):
        DoraMetricsResponse(
            project_id=uuid.uuid4(),
            window="7d",  # type: ignore[arg-type]
            authoritative_deployment_signal=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
            aggregates=valid_aggs,
            trend=[],
        )

    with pytest.raises(ValidationError):
        DoraMetricsResponse(
            project_id=uuid.uuid4(),
            window="14d",
            authoritative_deployment_signal="direct_cd_api",  # type: ignore[arg-type]
            aggregates=valid_aggs,
            trend=[],
        )

    dup_day = date(2026, 11, 16)
    with pytest.raises(ValidationError, match="Duplicate UTC date"):
        DoraMetricsResponse(
            project_id=uuid.uuid4(),
            window="14d",
            authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
            aggregates=valid_aggs,
            trend=[
                DoraDailyPoint(
                    date=dup_day,
                    lead_time_hours=None,
                    deployment_frequency_per_day=0.0,
                    change_failure_rate_pct=None,
                    mttr_hours=None,
                ),
                DoraDailyPoint(
                    date=dup_day,
                    lead_time_hours=10.0,
                    deployment_frequency_per_day=1.0,
                    change_failure_rate_pct=0.0,
                    mttr_hours=None,
                ),
            ],
        )


# ---------------------------------------------------------------------------
# US-30 / Task 30.2: Combined DORA Endpoint Assembly, RBAC, RLS & HTTP Tests
# ---------------------------------------------------------------------------


def _make_dora_auth_context() -> tuple[rsa.RSAPrivateKey, str, JWKSClient]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "dora-30-2-kid"
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


def _mint_dora_jwt(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    tenant_id: uuid.UUID,
    roles: list[str],
    sub: str = "dora-user",
) -> str:
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


@pytest.mark.asyncio
async def test_task_30_2_live_app_openapi_schema_exposes_both_dora_routes() -> None:
    """Verify the real FastAPI app exposes OpenAPI 3.1 contracts for both DORA endpoints."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/openapi.json")

    assert resp.status_code == 200
    openapi_doc = resp.json()
    paths = openapi_doc["paths"]

    for route_path in (
        "/api/v1/projects/{project_id}/dora-metrics",
        "/api/v1/dora/projects/{project_id}",
    ):
        assert route_path in paths
        op = paths[route_path]["get"]
        assert op.get("x-stub") is None
        assert "Stubs" not in op.get("tags", [])

        params = {p["name"]: p for p in op["parameters"]}
        assert params["project_id"]["in"] == "path"
        assert params["project_id"]["schema"]["format"] == "uuid"
        assert params["timeframe_days"]["in"] == "query"
        assert params["timeframe_days"]["schema"]["enum"] == [14, 30]
        assert params["timeframe_days"]["schema"]["default"] == 30

        resp_200 = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert resp_200 == {"$ref": "#/components/schemas/DoraMetricsResponse"}


def test_task_30_2_assemble_dora_metrics_combines_all_four_calculators() -> None:
    """
    Task 30.2: Combine US-26 (LT), US-27 (DF), US-28 (CFR), and US-29 (MTTR) results
    into DoraMetricsResponse with nullable aggregates, signal source, notes, and daily trend.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    dep_success = Deployment(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        environment="production",
        commit_sha="a" * 40,
        status=DeploymentOutcome.SUCCESS.value,
        authoritative_signal_source=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK.value,
        deployed_at=datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc),
    )
    pr_item = PullRequest(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        repository_id=uuid.uuid4(),
        external_pr_number=101,
        title="feat: ship DORA metrics",
        state="merged",
        author_pseudonym_id="0" * 64,
        first_commit_at=datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc),
        merged_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
    )
    pf_resolved = ProductionFailure(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        status="RESOLVED",
        probe_source="webhook",
        severity="SEV-1",
    )

    lt_res = compute_lead_time_metrics(
        [(pr_item, dep_success)], window_days=14, window_end=window_end
    )
    df_res = compute_deployment_frequency([dep_success], window_days=14, window_end=window_end)
    cfr_res = compute_change_failure_rate([dep_success], window_days=14, window_end=window_end)
    mttr_res = compute_mttr([pf_resolved], window_days=14, window_end=window_end)

    assembled = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK,
        lead_time_result=lt_res,
        deployment_frequency_result=df_res,
        change_failure_rate_result=cfr_res,
        mttr_result=mttr_res,
    )

    assert assembled.project_id == pid
    assert assembled.window == "14d"
    assert (
        assembled.authoritative_deployment_signal == DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK
    )
    assert assembled.aggregates.lead_time_hours == 24.0
    assert assembled.aggregates.deployment_frequency_per_day == 0.07
    assert assembled.aggregates.change_failure_rate_pct == 0.0
    assert assembled.aggregates.mttr_hours == 1.8
    assert any("argocd_postsync_webhook" in note for note in assembled.notes)
    assert any("change_failure_rate_pct is 0.0%" in note for note in assembled.notes)
    assert len(assembled.trend) == 14

    nov_12 = next(pt for pt in assembled.trend if pt.date == date(2026, 11, 12))
    assert nov_12.lead_time_hours == 24.0
    assert nov_12.deployment_frequency_per_day == 1.0
    assert nov_12.change_failure_rate_pct == 0.0
    assert nov_12.mttr_hours == 1.8

    nov_11 = next(pt for pt in assembled.trend if pt.date == date(2026, 11, 11))
    assert nov_11.lead_time_hours is None
    assert nov_11.deployment_frequency_per_day == 0.0
    assert nov_11.change_failure_rate_pct is None
    assert nov_11.mttr_hours is None


def test_task_30_2_assemble_zero_telemetry_vs_failed_only_deployments() -> None:
    """
    Verify strict null vs 0.0 distinction:
    - 0 deployment events in 30d window -> all 4 aggregates and daily DF are None (null).
    - Failed-only deployments -> deployment_frequency_per_day is 0.0 and CFR is 100.0%.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    empty_resp = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=30,
        authoritative_deployment_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API,
        lead_time_result=compute_lead_time_metrics([], window_days=30, window_end=window_end),
        deployment_frequency_result=compute_deployment_frequency(
            [], window_days=30, window_end=window_end
        ),
        change_failure_rate_result=compute_change_failure_rate(
            [], window_days=30, window_end=window_end
        ),
        mttr_result=compute_mttr([], window_days=30, window_end=window_end),
    )

    assert empty_resp.window == "30d"
    assert empty_resp.aggregates.lead_time_hours is None
    assert empty_resp.aggregates.deployment_frequency_per_day is None
    assert empty_resp.aggregates.change_failure_rate_pct is None
    assert empty_resp.aggregates.mttr_hours is None
    assert len(empty_resp.trend) == 30
    assert all(pt.deployment_frequency_per_day is None for pt in empty_resp.trend)
    assert any("No authoritative production deployments" in note for note in empty_resp.notes)

    failed_dep = Deployment(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        environment="production",
        commit_sha="b" * 40,
        status=DeploymentOutcome.FAILED.value,
        authoritative_signal_source=DeploymentSignalSource.DIRECT_DEPLOYMENT_API.value,
        deployed_at=datetime(2026, 11, 13, 15, 0, 0, tzinfo=timezone.utc),
    )
    failed_only = assemble_dora_metrics_response(
        project_id=pid,
        timeframe_days=14,
        authoritative_deployment_signal=DeploymentSignalSource.DIRECT_DEPLOYMENT_API,
        lead_time_result=compute_lead_time_metrics([], window_days=14, window_end=window_end),
        deployment_frequency_result=compute_deployment_frequency(
            [failed_dep], window_days=14, window_end=window_end
        ),
        change_failure_rate_result=compute_change_failure_rate(
            [failed_dep], window_days=14, window_end=window_end
        ),
        mttr_result=compute_mttr([], window_days=14, window_end=window_end),
    )
    assert failed_only.aggregates.deployment_frequency_per_day == 0.0
    assert failed_only.aggregates.change_failure_rate_pct == 100.0
    assert failed_only.aggregates.lead_time_hours is None
    assert failed_only.aggregates.mttr_hours is None
    assert all(pt.deployment_frequency_per_day == 0.0 for pt in failed_only.trend)


@pytest.mark.asyncio
async def test_task_30_2_calculate_project_dora_metrics_service_and_404() -> None:
    """
    Verify calculate_project_dora_metrics queries project + all 4 domain calculators and
    raises HTTP 404 when the project is missing, soft-deleted, or belongs to another tenant.
    """
    tid = uuid.uuid4()
    pid = uuid.uuid4()
    window_end = datetime(2026, 11, 14, 18, 0, 0, tzinfo=timezone.utc)

    proj = Project(
        id=pid,
        tenant_id=tid,
        name="DORA Core API",
        slug="dora-core-api",
        authoritative_cd_signal=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
    )
    dep = Deployment(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        environment="production",
        commit_sha="c" * 40,
        status=DeploymentOutcome.SUCCESS.value,
        authoritative_signal_source=DeploymentSignalSource.GITHUB_DEPLOYMENTS_API.value,
        deployed_at=datetime(2026, 11, 12, 16, 0, 0, tzinfo=timezone.utc),
    )
    pr = PullRequest(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        repository_id=uuid.uuid4(),
        external_pr_number=42,
        title="feat: core telemetry",
        state="merged",
        author_pseudonym_id="1" * 64,
        first_commit_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        merged_at=datetime(2026, 11, 12, 15, 0, 0, tzinfo=timezone.utc),
    )
    pf = ProductionFailure(
        id=uuid.uuid4(),
        tenant_id=tid,
        project_id=pid,
        started_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 11, 12, 11, 48, 0, tzinfo=timezone.utc),
        status="RESOLVED",
        probe_source="webhook",
        severity="SEV-1",
    )

    class _MockResult:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def all(self) -> list[Any]:
            return self._items

        def scalars(self) -> "_MockResult":
            return self

        def first(self) -> Any:
            return self._items[0] if self._items else None

    class _MockSession:
        def __init__(self, *, found_project: Project | None) -> None:
            self._project = found_project

        async def execute(self, stmt: Any) -> _MockResult:
            entities = [desc.get("entity") for desc in stmt.column_descriptions]
            if entities == [Project]:
                return _MockResult([self._project] if self._project is not None else [])
            if entities == [PullRequest, Deployment]:
                return _MockResult([(pr, dep)] if self._project is not None else [])
            if entities == [Deployment]:
                return _MockResult([dep] if self._project is not None else [])
            if entities == [Deployment, Deployment.incident_links.property.mapper.class_]:
                return _MockResult([(dep, None)] if self._project is not None else [])
            if entities == [ProductionFailure]:
                return _MockResult([pf] if self._project is not None else [])
            return _MockResult([])

    res = await calculate_project_dora_metrics(
        _MockSession(found_project=proj),  # type: ignore[arg-type]
        tenant_id=tid,
        project_id=pid,
        timeframe_days=14,
        window_end=window_end,
    )
    assert res.project_id == pid
    assert res.window == "14d"
    assert res.authoritative_deployment_signal == DeploymentSignalSource.GITHUB_DEPLOYMENTS_API
    assert res.aggregates.lead_time_hours == 6.0
    assert res.aggregates.deployment_frequency_per_day == 0.07
    assert res.aggregates.change_failure_rate_pct == 0.0
    assert res.aggregates.mttr_hours == 1.8
    assert len(res.trend) == 14

    with pytest.raises(HTTPException) as exc_info:
        await calculate_project_dora_metrics(
            _MockSession(found_project=None),  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            timeframe_days=30,
            window_end=window_end,
        )
    assert exc_info.value.status_code == 404

    with pytest.raises(ValueError, match="Unsupported timeframe_days=7"):
        await calculate_project_dora_metrics(
            _MockSession(found_project=proj),  # type: ignore[arg-type]
            tenant_id=tid,
            project_id=pid,
            timeframe_days=7,  # type: ignore[arg-type]
            window_end=window_end,
        )


@pytest.mark.asyncio
async def test_task_30_2_http_endpoints_rbac_validation_404_and_200_contract() -> None:
    """
    Task 30.2 HTTP contract tests for both:
    - GET /api/v1/projects/{project_id}/dora-metrics
    - GET /api/v1/dora/projects/{project_id}
    """
    priv_key, kid, jwks_client = _make_dora_auth_context()
    orig_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = jwks_client

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    proj_id = uuid.uuid4()

    proj = Project(
        id=proj_id,
        tenant_id=tenant_a,
        name="HTTP DORA Project",
        slug="http-dora-project",
        authoritative_cd_signal=DeploymentSignalSource.ARGOCD_POSTSYNC_WEBHOOK.value,
    )

    class _EmptyOrProjectSession:
        def __init__(self, current_tenant: uuid.UUID) -> None:
            self._current_tenant = current_tenant

        async def execute(self, stmt: Any) -> Any:
            entities = [desc.get("entity") for desc in stmt.column_descriptions]

            class _Res:
                def __init__(self, items: list[Any]) -> None:
                    self._items = items

                def all(self) -> list[Any]:
                    return self._items

                def scalars(self) -> "_Res":
                    return self

                def first(self) -> Any:
                    return self._items[0] if self._items else None

            if entities == [Project] and self._current_tenant == tenant_a:
                return _Res([proj])
            return _Res([])

    @asynccontextmanager
    async def _fake_tenant_ctx(tid: uuid.UUID):
        yield _EmptyOrProjectSession(tid)

    async def _fake_resolve_roles(user: Any) -> set[str]:
        return {r.strip().upper() for r in user.roles}

    dev_token_a = _mint_dora_jwt(priv_key, kid=kid, tenant_id=tenant_a, roles=["DEVELOPER"])
    empty_role_token = _mint_dora_jwt(priv_key, kid=kid, tenant_id=tenant_a, roles=[])
    admin_token_b = _mint_dora_jwt(priv_key, kid=kid, tenant_id=tenant_b, roles=["TENANT_ADMIN"])

    try:
        with (
            mock.patch("app.dora.service.get_tenant_db_context", side_effect=_fake_tenant_ctx),
            mock.patch(
                "app.auth.permissions.resolve_caller_roles", side_effect=_fake_resolve_roles
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                for base_path in (
                    f"/api/v1/projects/{proj_id}/dora-metrics",
                    f"/api/v1/dora/projects/{proj_id}",
                ):
                    # 1. Unauthenticated -> 401
                    r_401 = await ac.get(base_path)
                    assert r_401.status_code == 401

                    # 2. Missing recognized roles -> 403
                    r_403 = await ac.get(
                        base_path,
                        headers={"Authorization": f"Bearer {empty_role_token}"},
                    )
                    assert r_403.status_code == 403
                    assert r_403.json() == FORBIDDEN_RESPONSE

                    # 3. Invalid timeframe_days / conflicting window -> 422
                    r_422 = await ac.get(
                        f"{base_path}?timeframe_days=7",
                        headers={"Authorization": f"Bearer {dev_token_a}"},
                    )
                    assert r_422.status_code == 422

                    r_conflict = await ac.get(
                        f"{base_path}?timeframe_days=14&window=30d",
                        headers={"Authorization": f"Bearer {dev_token_a}"},
                    )
                    assert r_conflict.status_code == 422

                    # 4. Cross-tenant -> 404 anti-enumeration
                    r_404 = await ac.get(
                        base_path,
                        headers={"Authorization": f"Bearer {admin_token_b}"},
                    )
                    assert r_404.status_code == 404
                    assert r_404.json() == {"detail": f"Project '{proj_id}' not found."}

                    # 5. Authorized DEVELOPER (default 30d & explicit 14d) -> 200 OK
                    r_30 = await ac.get(
                        base_path,
                        headers={"Authorization": f"Bearer {dev_token_a}"},
                    )
                    assert r_30.status_code == 200
                    body_30 = r_30.json()
                    assert body_30["project_id"] == str(proj_id)
                    assert body_30["window"] == "30d"
                    assert body_30["authoritative_deployment_signal"] == "argocd_postsync_webhook"
                    assert body_30["aggregates"] == {
                        "lead_time_hours": None,
                        "deployment_frequency_per_day": None,
                        "change_failure_rate_pct": None,
                        "mttr_hours": None,
                    }
                    assert len(body_30["notes"]) >= 1
                    assert len(body_30["trend"]) == 30

                    r_14 = await ac.get(
                        f"{base_path}?timeframe_days=14",
                        headers={"Authorization": f"Bearer {dev_token_a}"},
                    )
                    assert r_14.status_code == 200
                    body_14 = r_14.json()
                    assert body_14["window"] == "14d"
                    assert len(body_14["trend"]) == 14

                    r_win_14 = await ac.get(
                        f"{base_path}?window=14d",
                        headers={"Authorization": f"Bearer {dev_token_a}"},
                    )
                    assert r_win_14.status_code == 200
                    assert r_win_14.json()["window"] == "14d"
                    assert len(r_win_14.json()["trend"]) == 14
    finally:
        app.state.jwks_client = orig_client


def test_task_30_2_dora_package_exports_endpoint_services() -> None:
    """Verify app.dora exposes combined DORA metrics assembly and service helpers."""
    import app.dora as dora_pkg

    for symbol in (
        "assemble_dora_metrics_response",
        "build_dora_metrics_notes",
        "calculate_project_dora_metrics",
        "get_project_dora_metrics",
        "resolve_dora_timeframe_days",
    ):
        assert hasattr(dora_pkg, symbol)
        assert symbol in dora_pkg.__all__
