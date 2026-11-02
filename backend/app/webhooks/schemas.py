import uuid
from pydantic import BaseModel, ConfigDict, Field


class WebhookIntakeResponse(BaseModel):
    """HTTP 202 Accepted response for incoming webhook staging."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field("accepted", description="Status confirmation")
    duplicate: bool = Field(
        ..., description="True if redelivery was detected, False if newly staged"
    )
    provider: str = Field(..., description="Provider name (github, jira, slack, etc.)")
    delivery_id: str = Field(..., description="Provider delivery identifier")
    event_id: uuid.UUID | None = Field(
        None, description="Internal UUID of the staged webhook event"
    )
    message: str = Field(
        "Webhook accepted for asynchronous processing",
        description="Informational message",
    )
