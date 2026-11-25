"""Persistence for GitHub commits and pull requests (Task 20.4)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockers.service import (
    RiskSubjectType,
    RiskType,
    is_pr_open,
    reset_pr_staleness_on_activity,
    resolve_active_blocker_risks,
)
from app.db.models.models import Commit, PullRequest, PullRequestReview, Repository
from app.git.normalization import (
    NormalizedCommit,
    NormalizedPullRequest,
    NormalizedPullRequestReview,
    NormalizedPush,
)
from app.webhooks.metrics import record_dropped_stale_event
from app.webhooks.versioning import execute_versioned_update

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
            (
                await db.execute(
                    select(Repository).where(
                        Repository.tenant_id == tenant_id,
                        Repository.full_name == full_name,
                    )
                )
            )
            .scalars()
            .first()
        )
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
        await db.execute(select(Commit).where(Commit.tenant_id == tenant_id, Commit.sha == c.sha))
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
    latest_commit_ts: Optional[datetime] = None
    for c in n.commits:
        saved_commit = await _upsert_commit(db, tenant_id=tenant_id, repository_id=repo.id, c=c)
        if saved_commit is not None:
            saved += 1
            cand_ts = c.committed_at or c.authored_at
            if cand_ts is not None and (latest_commit_ts is None or cand_ts > latest_commit_ts):
                latest_commit_ts = cand_ts

    head_sha = n.commits[-1].sha
    open_prs = (
        (
            await db.execute(
                select(PullRequest).where(
                    PullRequest.tenant_id == tenant_id,
                    PullRequest.repository_id == repo.id,
                    PullRequest.head_branch == n.branch,
                    func.lower(PullRequest.state) == "open",
                    PullRequest.merged_at.is_(None),
                    PullRequest.closed_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for pr in open_prs:
        await _attach_chain(
            db, tenant_id=tenant_id, repository_id=repo.id, pr_id=pr.id, head_sha=head_sha
        )
        await _refresh_first_commit_at(db, tenant_id=tenant_id, pr=pr)
        if saved > 0:
            await reset_pr_staleness_on_activity(
                db,
                tenant_id=tenant_id,
                pr=pr,
                activity_at=latest_commit_ts or datetime.now(timezone.utc),
            )
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
            entity_version=n.incoming_version if n.incoming_version is not None else 1,
            created_in_git_at=n.created_at or datetime.now(timezone.utc),
            updated_at=n.updated_at or n.created_at or datetime.now(timezone.utc),
            merged_at=n.merged_at,
            closed_at=n.closed_at,
        )
        db.add(pr)
        await db.flush()
    else:
        if _is_stale(pr.state, n):
            record_dropped_stale_event("github")
            logger.warning(
                "STALE_OUT_OF_ORDER_REJECTED provider=github table=pull_requests "
                "record_id=%s pr_number=%s state=%s incoming_action=%s incoming_version=%s",
                pr.id,
                n.number,
                pr.state,
                n.action,
                n.incoming_version,
            )
            return f"noop:stale_pr_event:{n.number}"

        incoming_ver = (
            n.incoming_version if n.incoming_version is not None else ((pr.entity_version or 0) + 1)
        )
        update_values: dict[str, object] = {
            "title": n.title or pr.title,
            "state": n.state,
            "head_branch": n.head_branch or pr.head_branch,
            "base_branch": n.base_branch or pr.base_branch,
            "head_sha": n.head_sha or pr.head_sha,
            "is_stale": False,
            "updated_at": n.updated_at or datetime.now(timezone.utc),
        }
        if n.state == "MERGED":
            update_values["merged_at"] = n.merged_at or pr.merged_at
            update_values["merge_commit_sha"] = n.merge_commit_sha or pr.merge_commit_sha
            update_values["closed_at"] = n.closed_at or pr.closed_at
        elif n.state == "CLOSED":
            update_values["closed_at"] = n.closed_at or pr.closed_at
        else:  # reopened
            update_values["closed_at"] = None

        mut_res = await execute_versioned_update(
            db,
            table_name="pull_requests",
            record_id=pr.id,
            tenant_id=tenant_id,
            incoming_version=incoming_ver,
            values=update_values,
        )
        if not mut_res.applied:
            return f"noop:stale_pr_version:{n.number}"
        await db.refresh(pr)
        await resolve_active_blocker_risks(
            db,
            tenant_id=tenant_id,
            project_id=pr.project_id,
            risk_type=RiskType.STALE_PR,
            subject_type=RiskSubjectType.PULL_REQUEST,
            subject_id=pr.id,
        )

    await _attach_chain(
        db, tenant_id=tenant_id, repository_id=repo.id, pr_id=pr.id, head_sha=n.head_sha
    )
    await _refresh_first_commit_at(db, tenant_id=tenant_id, pr=pr)
    await db.flush()
    return f"persisted_pr:{pr.id}"


async def process_pull_request_review(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    n: NormalizedPullRequestReview,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Persists a GitHub `pull_request_review` event with contributor pseudonym isolation,
    resets `PullRequest.is_stale = False`, advances activity timestamp, and resolves
    any active `STALE_PR` Risk (TASK-307-1 / US-71).
    """
    repo = await _resolve_repository(
        db, tenant_id=tenant_id, external_id=n.repo_external_id, full_name=n.repo_full_name
    )
    if repo is None:
        logger.warning("Skipping PR review for unlinked repo %s", n.repo_full_name)
        return "noop:unlinked_github_repository"

    pr = (
        await db.execute(
            select(PullRequest).where(
                PullRequest.tenant_id == tenant_id,
                PullRequest.repository_id == repo.id,
                PullRequest.external_pr_number == n.pr_number,
            )
        )
    ).scalar_one_or_none()
    if pr is None:
        return f"noop:missing_pull_request:{n.pr_number}"

    if not is_pr_open(pr):
        return f"noop:review_on_non_open_pr:{n.pr_number}"

    reviewer_pseudo = await _pseudonym_id(
        db,
        tenant_id=tenant_id,
        login=n.reviewer_login,
        name=n.reviewer_name,
        email=n.reviewer_email,
    )
    if reviewer_pseudo is None:
        return f"noop:missing_pr_reviewer:{n.pr_number}"

    submitted_at = n.submitted_at or datetime.now(timezone.utc)
    review_state = (n.review_state or "COMMENTED").strip().upper()[:32] or "COMMENTED"

    existing_review = (
        await db.execute(
            select(PullRequestReview).where(
                PullRequestReview.tenant_id == tenant_id,
                PullRequestReview.pull_request_id == pr.id,
                PullRequestReview.reviewer_pseudonym_id == reviewer_pseudo,
                PullRequestReview.submitted_at == submitted_at,
            )
        )
    ).scalar_one_or_none()

    if existing_review is None:
        next_cycle = int(pr.review_cycles_count or 0) + 1
        review_row = PullRequestReview(
            tenant_id=tenant_id,
            pull_request_id=pr.id,
            integration_id=integration_id,
            reviewer_pseudonym_id=reviewer_pseudo,
            state=review_state,
            review_cycle_index=next_cycle,
            submitted_at=submitted_at,
        )
        db.add(review_row)
        pr.review_cycles_count = next_cycle
        if pr.first_review_at is None or submitted_at < pr.first_review_at:
            pr.first_review_at = submitted_at
        await db.flush()
    else:
        review_row = existing_review
        if review_row.state != review_state:
            review_row.state = review_state
            await db.flush()

    await reset_pr_staleness_on_activity(
        db,
        tenant_id=tenant_id,
        pr=pr,
        activity_at=submitted_at,
    )
    await db.flush()
    return f"persisted_pr_review:{review_row.id}"
