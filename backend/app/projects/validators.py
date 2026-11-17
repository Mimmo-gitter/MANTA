"""
SCRUM-378: GitHub Repository Identifier Validation & Normalization Service.

Validates GitHub repository identifiers:
- HTTPS URLs: https://github.com/owner/repo (.git optional)
- SSH URLs: git@github.com:owner/repo.git
- Slugs: owner/repo

Enforces:
- GitHub host requirement (rejects gitlab.com, bitbucket.org, etc. with HTTP 422).
- Valid owner and repository naming conventions.
- Clean canonical normalization to 'owner/repo'.
"""

import re
from typing import Tuple
from urllib.parse import urlparse

from fastapi import HTTPException, status

# GitHub username/org: alphanumeric characters, underscores, or hyphens
# GitHub repo name: 1-100 alphanumeric, hyphens, underscores, or periods (cannot be . or ..)
_OWNER_PATTERN = r"[a-zA-Z0-9_](?:[a-zA-Z0-9_-]*[a-zA-Z0-9_])?"
_REPO_PATTERN = r"[a-zA-Z0-9_.-]+"
_SLUG_REGEX = re.compile(rf"^{_OWNER_PATTERN}/{_REPO_PATTERN}$")
_SSH_REGEX = re.compile(rf"^git@github\.com:({_OWNER_PATTERN})/({_REPO_PATTERN})(?:\.git)?$")


def normalize_and_validate_github_repo(identifier: str) -> Tuple[str, str, str]:
    """
    Parses and normalizes a GitHub repository identifier.

    Args:
        identifier: A GitHub URL, SSH link, or 'owner/repo' slug.

    Returns:
        Tuple of (canonical_full_name, owner, repo) e.g. ('manta-org/manta-core', 'manta-org', 'manta-core').

    Raises:
        HTTPException(status_code=422) if the identifier is malformed or not hosted on GitHub.
    """
    if not identifier or not isinstance(identifier, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Repository identifier cannot be empty.",
        )

    clean = identifier.strip()

    # 1. SSH format: git@github.com:owner/repo.git
    ssh_match = _SSH_REGEX.match(clean)
    if ssh_match:
        owner, repo = ssh_match.group(1), ssh_match.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{owner}/{repo}", owner, repo

    # 2. HTTP/HTTPS or scheme-less URL
    if clean.startswith("http://") or clean.startswith("https://") or "github.com/" in clean:
        # Prepend scheme if missing for clean urlparse
        url_to_parse = clean if "://" in clean else f"https://{clean}"
        try:
            parsed = urlparse(url_to_parse)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Malformed repository URL: '{clean}'.",
            ) from exc

        host = (parsed.netloc or "").lower().split(":")[0]
        if host not in ("github.com", "www.github.com"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Only GitHub repositories are supported. Received host '{host}'.",
            )

        # Path should be /owner/repo or /owner/repo.git
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid GitHub repository path: '{parsed.path}'. Expected '/owner/repo'.",
            )

        owner, repo = path_parts[0], path_parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]

        full_name = f"{owner}/{repo}"
        if not _SLUG_REGEX.match(full_name) or repo in (".", ".."):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid GitHub repository name or owner in '{clean}'.",
            )
        return full_name, owner, repo

    # 3. Simple slug: owner/repo
    if clean.endswith(".git"):
        clean = clean[:-4]

    if _SLUG_REGEX.match(clean):
        parts = clean.split("/")
        owner, repo = parts[0], parts[1]
        if repo in (".", ".."):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid repository name: '{repo}'.",
            )
        return clean, owner, repo

    # Reject any other malformed input
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Invalid GitHub repository identifier '{clean}'. Must be 'owner/repo' or a valid GitHub URL.",
    )
