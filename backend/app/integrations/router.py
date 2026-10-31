"""
SCRUM-322 & SCRUM-323: Integrations API Router.

Features:
- Provider connection with live credential verification (GitHub, Jira, Slack).
- Rejection of invalid credentials with HTTP 400 without storing any data.
- Strict administrator authorization (TENANT_ADMIN) with HTTP 403 on non-admin users.
- Automatic AES-256-GCM encryption of stored secrets via encryption_service (SCRUM-321).
- Cryptographically unguessable UUIDv4 webhook URLs bound 1-to-1 to integration and tenant (SCRUM-323).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import TokenClaims
from app.integrations.schemas import (
    GitHubConnectRequest,
    IntegrationConnectResponse,
    IntegrationSummaryResponse,
    JiraConnectRequest,
    SlackConnectRequest,
)
from app.integrations.service import (
    delete_tenant_integration,
    generate_webhook_secret,
    get_tenant_integration,
    list_tenant_integrations,
    save_or_update_integration,
)
from app.integrations.validators import (
    InvalidCredentialsError,
    validate_github_credentials,
    validate_jira_credentials,
    validate_slack_credentials,
)

logger = logging.getLogger("app.integrations.router")

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])


def require_tenant_admin(user: TokenClaims = Depends(get_current_user)) -> TokenClaims:
    """
    Enforces that the current authenticated user has administrator privileges.
    Returns TokenClaims if valid, otherwise raises HTTP 403 Forbidden.
    """
    roles_upper = {r.upper() for r in user.roles}
    admin_roles = {"TENANT_ADMIN", "ADMIN", "ORGANIZATION_ADMIN"}
    if not (roles_upper & admin_roles):
        logger.warning(
            f"User '{user.sub}' with roles {user.roles} denied access to integration management"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Tenant administrator privilege required",
        )
    return user


@router.post(
    "/github/connect",
    response_model=IntegrationConnectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect GitHub integration with live credential validation",
)
async def connect_github(
    payload: GitHubConnectRequest,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> IntegrationConnectResponse:
    """
    SCRUM-322: Validates GitHub personal access token / OAuth token against GitHub API.
    SCRUM-323: Generates unique webhook URL for GitHub webhook intake.
    """
    try:
        external_account_id = await validate_github_credentials(payload.access_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raw_wh_secret = payload.webhook_secret or generate_webhook_secret()
    api_creds = {"access_token": payload.access_token}

    return await save_or_update_integration(
        tenant_id=user.tenant_uuid,
        provider="github",
        external_account_id=external_account_id,
        raw_webhook_secret=raw_wh_secret,
        api_credentials=api_creds,
        base_url=str(request.base_url),
    )


@router.post(
    "/jira/connect",
    response_model=IntegrationConnectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect Jira integration with live credential validation",
)
async def connect_jira(
    payload: JiraConnectRequest,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> IntegrationConnectResponse:
    """
    SCRUM-322: Validates Jira email and API token against Jira REST API.
    SCRUM-323: Generates unique webhook URL for Jira webhook intake.
    """
    try:
        external_account_id = await validate_jira_credentials(
            base_url=payload.base_url,
            email=payload.email,
            api_token=payload.api_token,
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raw_wh_secret = payload.webhook_secret or generate_webhook_secret()
    api_creds = {
        "base_url": payload.base_url,
        "email": payload.email,
        "api_token": payload.api_token,
    }

    return await save_or_update_integration(
        tenant_id=user.tenant_uuid,
        provider="jira",
        external_account_id=external_account_id,
        raw_webhook_secret=raw_wh_secret,
        api_credentials=api_creds,
        base_url=str(request.base_url),
    )


@router.post(
    "/slack/connect",
    response_model=IntegrationConnectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connect Slack integration with live credential validation",
)
async def connect_slack(
    payload: SlackConnectRequest,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> IntegrationConnectResponse:
    """
    SCRUM-322: Validates Slack Bot token against Slack API auth.test.
    SCRUM-323: Generates unique webhook URL for Slack webhook intake.
    """
    try:
        external_account_id = await validate_slack_credentials(payload.bot_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    raw_wh_secret = payload.signing_secret
    api_creds = {"bot_token": payload.bot_token}

    return await save_or_update_integration(
        tenant_id=user.tenant_uuid,
        provider="slack",
        external_account_id=external_account_id,
        raw_webhook_secret=raw_wh_secret,
        api_credentials=api_creds,
        base_url=str(request.base_url),
    )


@router.get(
    "",
    response_model=list[IntegrationSummaryResponse],
    summary="List integrations for the current tenant",
)
async def list_integrations(
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> list[IntegrationSummaryResponse]:
    """Lists all integrations configured for the tenant with masked secrets."""
    return await list_tenant_integrations(user.tenant_uuid, str(request.base_url))


@router.get(
    "/{integration_id}",
    response_model=IntegrationSummaryResponse,
    summary="Get an integration by ID",
)
async def get_integration(
    integration_id: uuid.UUID,
    request: Request,
    user: TokenClaims = Depends(require_tenant_admin),
) -> IntegrationSummaryResponse:
    """Retrieves an integration for the tenant by ID."""
    res = await get_tenant_integration(user.tenant_uuid, integration_id, str(request.base_url))
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration '{integration_id}' not found",
        )
    return res


@router.delete(
    "/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect and remove an integration",
)
async def delete_integration(
    integration_id: uuid.UUID,
    user: TokenClaims = Depends(require_tenant_admin),
) -> Response:
    """Removes an integration and invalidates its webhook URL."""
    deleted = await delete_tenant_integration(user.tenant_uuid, integration_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Integration '{integration_id}' not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
