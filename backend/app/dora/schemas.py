"""Pydantic schemas for DORA metrics (US-26 / Task 26.3, US-27 / Task 27.2, US-28 / Task 28.2, US-29 / Task 29.2 & US-30 / Task 30.1)."""

import datetime as dt
from datetime import date, datetime
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.deployments.schemas import DeploymentSignalSource


class DailyDeploymentCount(BaseModel):
    """Daily count of successful authoritative production deployments in UTC."""

    date: date
    count: int = Field(ge=0)


class DeploymentFrequencyResult(BaseModel):
    """Rolling 14-day or 30-day Deployment Frequency calculation result."""

    window_days: Literal[14, 30]
    successful_deployment_count: int = Field(ge=0)
    deployments_per_day: float = Field(ge=0.0)
    daily_series: list[DailyDeploymentCount]
    window_start: datetime
    window_end: datetime


class PullRequestLeadTimeItem(BaseModel):
    """Per-PR Lead Time (T_prod_deployed - T_first_commit) attributed to contributor pseudonyms."""

    pull_request_id: uuid.UUID
    deployment_id: uuid.UUID
    repository_id: Optional[uuid.UUID] = None
    external_pr_number: int
    author_pseudonym_id: str
    actor_pseudonym_id: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    head_sha: Optional[str] = None
    first_commit_at: datetime
    deployed_at: datetime
    lead_time_seconds: float = Field(ge=0.0)
    lead_time_hours: float = Field(ge=0.0)


class DailyLeadTimeMetric(BaseModel):
    """Daily Lead Time trend point in UTC using continuous median (percentile_cont(0.5))."""

    date: date
    deployed_pr_count: int = Field(ge=0)
    median_lead_time_seconds: Optional[float] = None
    median_lead_time_hours: Optional[float] = None


class LeadTimeResult(BaseModel):
    """Rolling 14-day or 30-day Lead Time for Changes calculation result."""

    window_days: Literal[14, 30]
    deployed_pr_count: int = Field(ge=0)
    median_lead_time_seconds: Optional[float] = None
    median_lead_time_hours: Optional[float] = None
    pull_requests: list[PullRequestLeadTimeItem] = Field(default_factory=list)
    daily_series: list[DailyLeadTimeMetric]
    window_start: datetime
    window_end: datetime


class DailyChangeFailureRateMetric(BaseModel):
    """Daily Change Failure Rate trend point in UTC."""

    date: date
    total_deployment_count: int = Field(ge=0)
    failed_deployment_count: int = Field(ge=0)
    change_failure_rate: Optional[float] = None
    change_failure_rate_percentage: Optional[float] = None

    @property
    def cfr(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_ratio(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_percentage(self) -> Optional[float]:
        return self.change_failure_rate_percentage


class ChangeFailureRateResult(BaseModel):
    """Rolling 14-day or 30-day Change Failure Rate calculation result (US-28 / Task 28.2)."""

    window_days: Literal[14, 30]
    total_deployment_count: int = Field(ge=0)
    failed_deployment_count: int = Field(ge=0)
    change_failure_rate: Optional[float] = None
    change_failure_rate_percentage: Optional[float] = None
    failed_deployment_ids: list[uuid.UUID] = Field(default_factory=list)
    daily_series: list[DailyChangeFailureRateMetric]
    window_start: datetime
    window_end: datetime

    @property
    def cfr(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_ratio(self) -> Optional[float]:
        return self.change_failure_rate

    @property
    def cfr_percentage(self) -> Optional[float]:
        return self.change_failure_rate_percentage


class DailyMTTRMetric(BaseModel):
    """Daily Mean Time to Recovery trend point in UTC (US-29 / Task 29.2)."""

    date: date
    resolved_outage_count: int = Field(ge=0)
    mttr_seconds: Optional[float] = None
    mttr_hours: Optional[float] = None
    mttr_minutes: Optional[float] = None


class MTTROutageItem(BaseModel):
    """Detailed item representation of an individual resolved outage session (US-29)."""

    incident_id: uuid.UUID
    project_id: uuid.UUID
    started_at: datetime
    resolved_at: datetime
    duration_seconds: float = Field(ge=0.0)
    duration_hours: float = Field(ge=0.0)
    severity: str = "SEV-2"


class MTTRResult(BaseModel):
    """Rolling 14-day or 30-day Mean Time to Recovery (MTTR) calculation result (US-29 / Task 29.2)."""

    window_days: Literal[14, 30]
    resolved_outage_count: int = Field(ge=0)
    mttr_seconds: Optional[float] = None
    mttr_hours: Optional[float] = None
    mttr_minutes: Optional[float] = None
    outages: list[MTTROutageItem] = Field(default_factory=list)
    daily_series: list[DailyMTTRMetric]
    window_start: datetime
    window_end: datetime

    @property
    def mttr(self) -> Optional[float]:
        """Convenience accessor returning MTTR in hours."""
        return self.mttr_hours


# ---------------------------------------------------------------------------
# US-30 / Task 30.1: OpenAPI 3.1 API Contract for Project DORA Metrics
# ---------------------------------------------------------------------------

DoraWindow = Literal["14d", "30d"]
DoraTimeframeDays = Literal[14, 30]


class DoraAggregates(BaseModel):
    """
    Rolling-window DORA metric aggregates (OpenAPI 3.1).

    Every field is explicitly required and nullable (`number | null`):
    - `null` means the metric is undefined due to 0 qualifying events in the window.
    - `0.0` means events occurred and the measured value is zero (e.g., `0.0%` CFR
      when deployments succeeded with 0 failures or linked incidents).
    """

    model_config = ConfigDict(
        allow_inf_nan=False,
        json_schema_extra={
            "examples": [
                {
                    "lead_time_hours": 23.5,
                    "deployment_frequency_per_day": 0.43,
                    "change_failure_rate_pct": 0.0,
                    "mttr_hours": None,
                }
            ]
        },
    )

    lead_time_hours: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Continuous median Lead Time for Changes in hours across shipped PRs "
            "(T_prod_deployed - T_first_commit). Returns null when 0 shipped PRs exist in the window."
        ),
    )
    deployment_frequency_per_day: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Rolling average of authoritative SUCCESS production deployments per day "
            "(count / window_days, rounded to 2 decimals). Returns null when deployment telemetry is absent."
        ),
    )
    change_failure_rate_pct: float | None = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Percentage [0.0, 100.0] of authoritative production deployments that FAILED, "
            "ROLLED_BACK, or triggered a CONFIRMED Jira incident within 24h (distinct union). "
            "Returns null when total production deployments == 0; returns 0.0 when >= 1 deployment succeeded with 0 failures."
        ),
    )
    mttr_hours: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Mean Time to Recovery in hours across resolved production health failure sessions "
            "(resolved_at - started_at). Returns null when 0 resolved failure sessions exist; "
            "returns 0.0 on instantaneous restoration."
        ),
    )


class DoraDailyPoint(BaseModel):
    """Single UTC calendar day bucket in the DORA daily trend series (OpenAPI 3.1)."""

    model_config = ConfigDict(allow_inf_nan=False)

    date: dt.date = Field(
        ...,
        description="UTC calendar date (ISO-8601 YYYY-MM-DD).",
    )
    lead_time_hours: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Daily continuous median Lead Time in hours, or null if 0 PRs deployed on this UTC date."
        ),
    )
    deployment_frequency_per_day: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Successful authoritative production deployments on this UTC date, "
            "or null if telemetry is unavailable."
        ),
    )
    change_failure_rate_pct: float | None = Field(
        ...,
        ge=0.0,
        le=100.0,
        description=(
            "Daily Change Failure Rate percentage [0.0, 100.0], "
            "or null if 0 production deployments occurred on this UTC date."
        ),
    )
    mttr_hours: float | None = Field(
        ...,
        ge=0.0,
        description=(
            "Daily Mean Time to Recovery in hours for incidents resolved on this UTC date, "
            "or null if 0 resolved incidents."
        ),
    )


class DoraMetricsResponse(BaseModel):
    """
    OpenAPI 3.1 response contract for GET /api/v1/projects/{id}/dora-metrics
    and GET /api/v1/dora/projects/{project_id} (US-30 / Task 30.1).
    """

    model_config = ConfigDict(
        allow_inf_nan=False,
        json_schema_extra={
            "examples": [
                {
                    "project_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "window": "14d",
                    "authoritative_deployment_signal": "github_deployments_api",
                    "aggregates": {
                        "lead_time_hours": 23.5,
                        "deployment_frequency_per_day": 0.43,
                        "change_failure_rate_pct": 0.0,
                        "mttr_hours": None,
                    },
                    "notes": [
                        "mttr_hours is null: 0 resolved production health failure sessions in the 14d window.",
                        "change_failure_rate_pct is 0.0%: 6 production deployments succeeded with 0 failures or linked incidents.",
                    ],
                    "trend": [
                        {
                            "date": "2026-11-16",
                            "lead_time_hours": 23.5,
                            "deployment_frequency_per_day": 2.0,
                            "change_failure_rate_pct": 0.0,
                            "mttr_hours": None,
                        }
                    ],
                }
            ]
        },
    )

    project_id: uuid.UUID = Field(
        ...,
        description="Target project UUID scoped to the authenticated tenant.",
    )
    window: DoraWindow = Field(
        ...,
        description="Rolling evaluation window identifier ('14d' or '30d').",
    )
    authoritative_deployment_signal: DeploymentSignalSource = Field(
        ...,
        description="Project's configured authoritative production CD signal source.",
    )
    aggregates: DoraAggregates = Field(
        ...,
        description="Rolling-window aggregate DORA metrics with explicit null vs 0.0 semantics.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Human-readable diagnostic notes explaining null metrics, signal filtering, or rounding.",
    )
    trend: list[DoraDailyPoint] = Field(
        ...,
        description=(
            "Contiguous UTC daily trend series ordered chronologically "
            "(14 or 30 points matching window)."
        ),
    )

    @field_validator("trend")
    @classmethod
    def _ensure_unique_trend_dates(cls, points: list[DoraDailyPoint]) -> list[DoraDailyPoint]:
        """Reject duplicate UTC calendar dates within a single trend series."""
        seen: set[dt.date] = set()
        for point in points:
            if point.date in seen:
                raise ValueError(f"Duplicate UTC date in trend series: {point.date.isoformat()}")
            seen.add(point.date)
        return points
