"""Persistence for GitHub commits and pull requests (Task 20.4)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.models import Commit, PullRequest, Repository
from app.git.normalization import NormalizedCommit, NormalizedPullRequest, NormalizedPush

# TODO: verify import path / signature of the Task 20.3 helper.
from app.contributors.service import resolve_or_create_contributor_pseudonym

logger = logging.getLogger("app.git.service")


async def _resolve_repository(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    external_id: Optional[str],
    full_name: Optional[str],
) -> Optional[Repository]:
    if external_id is not None:
        try:
            ext = int(external_id)
        except (TypeError, ValueError):
            ext = None
        if ext is not None:
            repo = (
                await db.execute(
                    select(Repository).where(
                        Repository.tenant_id == tenant_id,
                        Repository.external_repo_id == ext,
                    )
                )
            ).scalar_one_or_none()
            if repo is not None:
                return repo
    if full_name:
        return (
            await db.execute(
                select(Repository).where(
                    Repository.tenant_id == tenant_id,
                    Repository.full_name == full_name,
                )
            )
        ).scalars().first()
    return None


async def _pseudonym_id(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    login: Optional[str],
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[str]:
    """Single adapter around the Task 20.3 helper (returns pseudonym_id string)."""
    external = login or email
    if not external:
        return None
    pseudonym_id = await resolve_or_create_contributor_pseudonym(
        db,
        tenant_id,
        "github",
        external,
        raw_email=email,
        raw_name=name or login,
        raw_handle=login,
    )
    return pseudonym_id or None


async def _refresh_first_commit_at(
    db: AsyncSession, *, tenant_id: uuid.UUID, pr: PullRequest
) -> None:
    earliest = (
        await db.execute(
            select(func.min(Commit.authored_at)).where(
                Commit.tenant_id == tenant_id,
                Commit.pull_request_id == pr.id,
            )
        )
    ).scalar_one_or_none()
    if earliest is not None:
        pr.first_commit_at = earliest


async def _attach_chain(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    pr_id: uuid.UUID,
    head_sha: Optional[str],
    limit: int = 500,
) -> None:
    """Walk head_sha -> parent_shas and attach commits of the branch to the PR.
    Stops at an unknown commit or at a commit that belongs to another PR."""
    seen: set[str] = set()
    cur = head_sha
    while cur and cur not in seen and len(seen) < limit:
        seen.add(cur)
        c = (
            await db.execute(
                select(Commit).where(
                    Commit.tenant_id == tenant_id,
                    Commit.repository_id == repository_id,
                    Commit.sha == cur,
                )
            )
        ).scalar_one_or_none()
        if c is None:
            break
        if c.pull_request_id not in (None, pr_id):
            break
        c.pull_request_id = pr_id
        parents = c.parent_shas or []
        cur = parents[0] if parents else None
    await db.flush()


async def _upsert_commit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    c: NormalizedCommit,
) -> Optional[Commit]:
    author_id = await _pseudonym_id(
        db, tenant_id=tenant_id, login=c.author_login, name=c.author_name, email=c.author_email
    )
    if author_id is None or c.authored_at is None:
        logger.warning("Skipping commit %s: no author identity or timestamp", c.sha)
        return None
    committer_id = (
        await _pseudonym_id(
            db,
            tenant_id=tenant_id,
            login=c.committer_login,
            name=c.committer_name,
            email=c.committer_email,
        )
        or ""
    )

    # commits.sha is the primary key: look it up by sha and verify the repository.
    row = (
        await db.execute(
            select(Commit).where(Commit.tenant_id == tenant_id, Commit.sha == c.sha)
        )
    ).scalar_one_or_none()
    if row is not None and row.repository_id != repository_id:
        logger.warning("Commit %s already stored for another repository, skipping", c.sha)
        return None

    if row is None:
        row = Commit(
            tenant_id=tenant_id,
            repository_id=repository_id,
            sha=c.sha,
            author_pseudonym_id=author_id,
            committer_pseudonym_id=committer_id,
            lines_added=0,  # push payload has no line counts
            lines_deleted=0,
            authored_at=c.authored_at,
            committed_at=c.committed_at or c.authored_at,
            parent_shas=list(c.parent_shas),
            is_root_of_branch=c.is_root_of_branch,
        )
        db.add(row)
    else:
        row.author_pseudonym_id = author_id
        if committer_id:
            row.committer_pseudonym_id = committer_id
        if c.parent_shas and not row.parent_shas:
            row.parent_shas = list(c.parent_shas)
        row.is_root_of_branch = row.is_root_of_branch or c.is_root_of_branch
    await db.flush()
    return row


async def process_push(db: AsyncSession, *, tenant_id: uuid.UUID, n: NormalizedPush) -> str:
    repo = await _resolve_repository(
        db, tenant_id=tenant_id, external_id=n.repo_external_id, full_name=n.repo_full_name
    )
    if repo is None:
        logger.warning("Skipping push for unlinked repo %s", n.repo_full_name)
        return "noop:unlinked_github_repository"

    saved = 0
    for c in n.commits:
        if await _upsert_commit(db, tenant_id=tenant_id, repository_id=repo.id, c=c):
            saved += 1

    head_sha = n.commits[-1].sha
    open_prs = (
        await db.execute(
            select(PullRequest).where(
                PullRequest.tenant_id == tenant_id,
                PullRequest.repository_id == repo.id,
                PullRequest.head_branch == n.branch,
                PullRequest.state == "OPEN",
            )
        )
    ).scalars().all()
    for pr in open_prs:
        await _attach_chain(
            db, tenant_id=tenant_id, repository_id=repo.id, pr_id=pr.id, head_sha=head_sha
        )
        await _refresh_first_commit_at(db, tenant_id=tenant_id, pr=pr)
    await db.flush()
    return f"persisted_push:{saved}"


def _is_stale(existing_state: str, incoming: NormalizedPullRequest) -> bool:
    if existing_state == "MERGED":
        return True  # MERGED is terminal
    if existing_state == "CLOSED" and incoming.state == "OPEN" and incoming.action != "reopened":
        return True  # delayed opened/synchronize after close
    return False


async def process_pull_request(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    n: NormalizedPullRequest,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    repo = await _resolve_repository(
        db, tenant_id=tenant_id, external_id=n.repo_external_id, full_name=n.repo_full_name
    )
    if repo is None:
        logger.warning("Skipping PR for unlinked repo %s", n.repo_full_name)
        return "noop:unlinked_github_repository"

    pr = (
        await db.execute(
            select(PullRequest).where(
                PullRequest.tenant_id == tenant_id,
                PullRequest.repository_id == repo.id,
                PullRequest.external_pr_number == n.number,
            )
        )
    ).scalar_one_or_none()

    if pr is None:
        author_id = await _pseudonym_id(db, tenant_id=tenant_id, login=n.author_login)
        if author_id is None:
            return f"noop:missing_pr_author:{n.number}"
        if not (n.head_branch and n.base_branch and n.head_sha):
            return f"noop:incomplete_pr_payload:{n.number}"
        pr = PullRequest(
            tenant_id=tenant_id,
            project_id=repo.project_id,
            repository_id=repo.id,
            integration_id=integration_id,
            author_pseudonym_id=author_id,
            external_pr_number=n.number,
            title=n.title or "",
            state=n.state,
            head_branch=n.head_branch,
            base_branch=n.base_branch,
            head_sha=n.head_sha,
            merge_commit_sha=n.merge_commit_sha,
            created_in_git_at=n.created_at or datetime.now(timezone.utc),
            merged_at=n.merged_at,
            closed_at=n.closed_at,
        )
        db.add(pr)
    else:
        if _is_stale(pr.state, n):
            return f"noop:stale_pr_event:{n.number}"
        pr.title = n.title or pr.title
        pr.state = n.state
        pr.head_branch = n.head_branch or pr.head_branch
        pr.base_branch = n.base_branch or pr.base_branch
        pr.head_sha = n.head_sha or pr.head_sha
        if n.state == "MERGED":
            pr.merged_at = n.merged_at or pr.merged_at
            pr.merge_commit_sha = n.merge_commit_sha or pr.merge_commit_sha
            pr.closed_at = n.closed_at or pr.closed_at
        elif n.state == "CLOSED":
            pr.closed_at = n.closed_at or pr.closed_at
        else:  # reopened
            pr.closed_at = None
        pr.entity_version = (pr.entity_version or 0) + 1
    await db.flush()  # session has autoflush=False; pr.id must exist before linking

    await _attach_chain(
        db, tenant_id=tenant_id, repository_id=repo.id, pr_id=pr.id, head_sha=n.head_sha
    )
    await _refresh_first_commit_at(db, tenant_id=tenant_id, pr=pr)
    await db.flush()
    return f"persisted_pr:{pr.id}"
