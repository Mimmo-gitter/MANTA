"""
SCRUM-322 & SCRUM-323: Pydantic schemas for Provider Connection & Webhook URLs.
"""

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, Field, field_validator


class GitHubConnectRequest(BaseModel):
    """Payload to connect a GitHub integration."""

    access_token: str = Field(
        ...,
        min_length=1,
        description="GitHub Personal Access Token or OAuth Access Token",
    )
    webhook_secret: Optional[str] = Field(
        None,
        min_length=8,
        description=(
            "Optional custom webhook signing secret. "
            "If omitted, a cryptographically secure 32-byte secret is automatically generated."
        ),
    )

    @field_validator("access_token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("GitHub access token cannot be empty")
        return val


class JiraConnectRequest(BaseModel):
    """Payload to connect an Atlassian Jira integration."""

    base_url: str = Field(
        ...,
        description="Jira instance URL (e.g. https://mycompany.atlassian.net)",
    )
    email: str = Field(..., description="Atlassian account email address")
    api_token: str = Field(..., min_length=1, description="Atlassian API token")
    webhook_secret: Optional[str] = Field(
        None,
        min_length=8,
        description=(
            "Optional custom webhook signing secret. "
            "If omitted, a cryptographically secure 32-byte secret is automatically generated."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        val = v.strip().rstrip("/")
        if not (val.startswith("http://") or val.startswith("https://")):
            raise ValueError("Jira base_url must start with http:// or https://")
        return val

    @field_validator("email", "api_token")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("Field cannot be empty")
        return val


class SlackConnectRequest(BaseModel):
    """Payload to connect a Slack workspace integration."""

    bot_token: str = Field(
        ...,
        description="Slack Bot User OAuth Token (e.g. xoxb-...)",
    )
    signing_secret: str = Field(
        ...,
        min_length=1,
        description="Slack Signing Secret for verifying incoming webhooks",
    )

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("Slack bot token cannot be empty")
        return val

    @field_validator("signing_secret")
    @classmethod
    def validate_signing_secret(cls, v: str) -> str:
        val = v.strip()
        if not val:
            raise ValueError("Slack signing secret cannot be empty")
        return val


class IntegrationConnectResponse(BaseModel):
    """
    Response returned upon successful provider connection.
    Includes the unique unguessable webhook URL (SCRUM-323) and plaintext webhook secret.
    API credentials (tokens/passwords) are strictly NEVER returned.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    external_account_id: str
    status: str = "connected"
    webhook_url: str
    webhook_secret: Optional[str] = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class IntegrationSummaryResponse(BaseModel):
    """Safe representation of an active integration for listing and management."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    external_account_id: str
    status: str = "connected"
    webhook_url: str
    webhook_secret_masked: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
