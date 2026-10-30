"""
SCRUM-322: Provider Credential Validation Service.

Validates third-party API credentials against real provider endpoints (GitHub, Jira, Slack)
before persisting secrets into the database.
"""

import base64
import logging
from typing import Optional

import httpx

logger = logging.getLogger("app.integrations.validators")


class InvalidCredentialsError(Exception):
    """Raised when third-party provider credentials fail authentication or validation."""


async def validate_github_credentials(
    access_token: str,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """
    Validates GitHub credentials by querying GET https://api.github.com/user.
    Returns the GitHub account login (e.g. 'octocat').
    Raises InvalidCredentialsError if credentials are rejected, permissions are insufficient,
    or the GitHub API is unreachable.
    """
    token = access_token.strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "MANTA-Engineering-Intelligence/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        if client:
            resp = await client.get("https://api.github.com/user", headers=headers, timeout=10.0)
        else:
            async with httpx.AsyncClient() as default_client:
                resp = await default_client.get(
                    "https://api.github.com/user", headers=headers, timeout=10.0
                )

        if resp.status_code == 200:
            data = resp.json()
            login = data.get("login")
            if login:
                return str(login)
            user_id = data.get("id")
            if user_id:
                return f"gh_{user_id}"
            return "github_user"
        elif resp.status_code in (401, 403):
            msg = resp.json().get("message", "Bad credentials") if resp.content else "Unauthorized"
            raise InvalidCredentialsError(f"GitHub credential validation failed: {msg}")
        else:
            raise InvalidCredentialsError(
                f"GitHub API returned unexpected status {resp.status_code}"
            )
    except httpx.RequestError as exc:
        logger.error(f"GitHub API request error: {exc}")
        raise InvalidCredentialsError(f"Unable to reach GitHub API: {exc}") from exc


async def validate_jira_credentials(
    base_url: str,
    email: str,
    api_token: str,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """
    Validates Atlassian Jira credentials by querying GET {base_url}/rest/api/3/myself.
    Returns the Jira accountId or email.
    Raises InvalidCredentialsError if credentials are rejected or unreachable.
    """
    clean_base_url = base_url.strip().rstrip("/")
    url = f"{clean_base_url}/rest/api/3/myself"
    auth_str = f"{email.strip()}:{api_token.strip()}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")

    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Accept": "application/json",
        "User-Agent": "MANTA-Engineering-Intelligence/1.0",
    }

    try:
        if client:
            resp = await client.get(url, headers=headers, timeout=10.0)
        else:
            async with httpx.AsyncClient() as default_client:
                resp = await default_client.get(url, headers=headers, timeout=10.0)

        # Jira Server / Data Center may use /rest/api/2/myself instead of v3
        if resp.status_code == 404:
            url_v2 = f"{clean_base_url}/rest/api/2/myself"
            if client:
                resp = await client.get(url_v2, headers=headers, timeout=10.0)
            else:
                async with httpx.AsyncClient() as default_client:
                    resp = await default_client.get(url_v2, headers=headers, timeout=10.0)

        if resp.status_code == 200:
            data = resp.json()
            acc_id = data.get("accountId") or data.get("emailAddress") or data.get("name")
            if acc_id:
                return str(acc_id)
            return email.strip()
        elif resp.status_code in (401, 403):
            raise InvalidCredentialsError(
                "Jira credential validation failed: invalid email or API token"
            )
        else:
            raise InvalidCredentialsError(
                f"Jira API returned unexpected status {resp.status_code} at {url}"
            )
    except httpx.RequestError as exc:
        logger.error(f"Jira API request error: {exc}")
        raise InvalidCredentialsError(
            f"Unable to reach Jira instance at {clean_base_url}: {exc}"
        ) from exc


async def validate_slack_credentials(
    bot_token: str,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """
    Validates Slack Bot User OAuth token by calling POST https://slack.com/api/auth.test.
    Returns the Slack team_id (e.g. 'T01234567').
    Raises InvalidCredentialsError if credentials are rejected.
    """
    headers = {
        "Authorization": f"Bearer {bot_token.strip()}",
        "User-Agent": "MANTA-Engineering-Intelligence/1.0",
    }

    try:
        if client:
            resp = await client.post(
                "https://slack.com/api/auth.test", headers=headers, timeout=10.0
            )
        else:
            async with httpx.AsyncClient() as default_client:
                resp = await default_client.post(
                    "https://slack.com/api/auth.test", headers=headers, timeout=10.0
                )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                team_id = data.get("team_id") or data.get("team")
                if team_id:
                    return str(team_id)
                return "slack_workspace"
            else:
                err = data.get("error", "authentication_failed")
                raise InvalidCredentialsError(f"Slack credential validation failed: {err}")
        elif resp.status_code in (401, 403):
            raise InvalidCredentialsError("Slack credential validation failed: invalid bot token")
        else:
            raise InvalidCredentialsError(
                f"Slack API returned unexpected status {resp.status_code}"
            )
    except httpx.RequestError as exc:
        logger.error(f"Slack API request error: {exc}")
        raise InvalidCredentialsError(f"Unable to reach Slack API: {exc}") from exc
