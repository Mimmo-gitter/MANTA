"""Pydantic schemas for DORA Deployment Frequency metrics (US-27 / Task 27.2)."""

from datetime import date, datetime
from typing import Literal

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
