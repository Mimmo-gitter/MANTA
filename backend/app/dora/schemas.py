"""Pydantic schemas for DORA metrics (US-26 / Task 26.3, US-27 / Task 27.2 & US-28 / Task 28.2)."""

from datetime import date, datetime
from typing import Literal, Optional
import uuid

from pydantic import BaseModel, Field


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
