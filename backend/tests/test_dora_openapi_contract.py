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

from datetime import date, timedelta
import json
import uuid

from fastapi import APIRouter, FastAPI, Query
from fastapi.openapi.utils import get_openapi
from pydantic import ValidationError
import pytest

from app.deployments.schemas import DeploymentSignalSource
from app.dora.schemas import (
    DoraAggregates,
    DoraDailyPoint,
    DoraMetricsResponse,
    DoraTimeframeDays,
)


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
