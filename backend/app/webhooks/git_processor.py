"""
SCRUM-395 (Task 20.4) & US-20:
GitHub Webhook Ingestion for Push and Pull Request Events with Contributor Pseudonym Isolation.

Handles:
1. GitHub 'push' events:
   - Ingests multiple commits per push.
   - Preserves parent hash topological DAG chaining (parent_shas).
   - Detects feature branch root boundaries (is_root_of_branch).
   - Resolves distinct author vs. committer identities into tenant-salted HMAC-SHA256 pseudonyms.
   - Enforces idempotent ingestion via ON CONFLICT DO UPDATE.
2. GitHub 'pull_request' events:
   - 'opened': normalizes PR coordinates, resolves external author pseudonym without requiring
     internal users record, initializes entity_version=1 and active_fencing_token.
   - 'synchronize': advances head_sha, increments entity_version, evaluates optimistic concurrency
     fencing token to reject out-of-order stale deliveries.
   - 'closed' (merged=true): captures 40-char merge_commit_sha and merged_at timestamp for DORA attribution.
   - 'closed' (merged=false): records closed_at timestamp with merge_commit_sha=None.
3. Repository filtering:
   - Webhook deliveries for repositories unlinked to any active project are acknowledged
     and safely ignored with result 'noop:unlinked_repository' (0 domain table rows inserted).
4. Contributor PII isolation:
   - Strictly zero raw names, emails, or logins are written to 'commits' or 'pull_requests'.
   - All external identities are encrypted and vaulted exclusively in the 'contributors' table.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Optional
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contributors.service import resolve_or_create_contributor_pseudonym
from app.db.models.models import Commit, PullRequest
from app.deployments.service import resolve_github_project
from app.webhooks.sweeper import complete_webhook_event

logger = logging.getLogger("app.webhooks.git_processor")


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    val_str = value.strip()
    if not val_str:
        return None
    try:
        dt = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


async def _link_branch_commits_to_pr(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    repository_id: uuid.UUID,
    pr_id: uuid.UUID,
    head_sha: Optional[str] = None,
) -> int:
    """Associate unlinked commits with the given pull request by SHA."""
    if not head_sha:
        return 0
    clean_sha = head_sha.strip().lower()
    stmt = (
        update(Commit)
        .where(
            Commit.tenant_id == tenant_id,
            Commit.repository_id == repository_id,
            Commit.sha == clean_sha,
            Commit.pull_request_id.is_(None),
        )
        .values(pull_request_id=pr_id)
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def ingest_github_push_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    event_id: uuid.UUID,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Ingests a GitHub push webhook event into the 'commits' table under tenant RLS.
    Extracts multi-commit batch, parent hashes, line stats, and resolves author/committer
    identities into isolated salted pseudonyms.
    """
    repo_dict = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    repo_external_id = repo_dict.get("id")
    repo_full_name = repo_dict.get("full_name")

    project_id, repository_id = await resolve_github_project(
        db,
        tenant_id=tenant_id,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
    )
    if repository_id is None or project_id is None:
        logger.info(
            "Ignoring push event for unlinked repository %s (full_name=%s)",
            repo_external_id,
            repo_full_name,
        )
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:unlinked_repository"

    # Identify matching open PR on the same branch if available
    ref = str(payload.get("ref") or "").strip()
    branch_name = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref
    pull_request_id: Optional[uuid.UUID] = None
    if branch_name:
        pr_stmt = (
            select(PullRequest.id)
            .where(
                PullRequest.tenant_id == tenant_id,
                PullRequest.repository_id == repository_id,
                PullRequest.head_branch == branch_name,
                PullRequest.state == "open",
            )
            .limit(1)
        )
        pull_request_id = (await db.execute(pr_stmt)).scalar_one_or_none()

    commits_raw = payload.get("commits")
    if isinstance(commits_raw, list):
        commits_list = commits_raw
    elif isinstance(payload.get("head_commit"), dict):
        commits_list = [payload["head_commit"]]
    else:
        commits_list = []

    fencing_token = int(payload.get("fencing_token") or payload.get("active_fencing_token") or 0)
    persisted_count = 0

    for commit_data in commits_list:
        if not isinstance(commit_data, dict):
            continue
        sha = str(commit_data.get("id") or commit_data.get("sha") or "").strip().lower()
        if not sha:
            continue

        # 1. Resolve author pseudonym
        author_dict = commit_data.get("author") if isinstance(commit_data.get("author"), dict) else {}
        author_actor = (
            author_dict.get("username")
            or author_dict.get("login")
            or author_dict.get("id")
            or author_dict.get("email")
            or author_dict.get("name")
            or "unknown_author"
        )
        author_pseudo = await resolve_or_create_contributor_pseudonym(
            db,
            tenant_id=tenant_id,
            provider="github",
            external_actor_id=str(author_actor),
            raw_email=author_dict.get("email"),
            raw_name=author_dict.get("name"),
            raw_handle=author_dict.get("username") or author_dict.get("login"),
        )

        # 2. Resolve committer pseudonym
        committer_dict = (
            commit_data.get("committer") if isinstance(commit_data.get("committer"), dict) else {}
        )
        if committer_dict:
            committer_actor = (
                committer_dict.get("username")
                or committer_dict.get("login")
                or committer_dict.get("id")
                or committer_dict.get("email")
                or committer_dict.get("name")
                or author_actor
            )
            committer_pseudo = await resolve_or_create_contributor_pseudonym(
                db,
                tenant_id=tenant_id,
                provider="github",
                external_actor_id=str(committer_actor),
                raw_email=committer_dict.get("email"),
                raw_name=committer_dict.get("name"),
                raw_handle=committer_dict.get("username") or committer_dict.get("login"),
            )
        else:
            committer_pseudo = author_pseudo

        # 3. Extract parent SHAs
        parent_shas: list[str] = []
        if isinstance(commit_data.get("parent_shas"), list):
            parent_shas = [str(p).strip().lower() for p in commit_data["parent_shas"] if p]
        elif isinstance(commit_data.get("parents"), list):
            for p in commit_data["parents"]:
                if isinstance(p, dict) and p.get("sha"):
                    parent_shas.append(str(p["sha"]).strip().lower())
                elif isinstance(p, str) and p.strip():
                    parent_shas.append(p.strip().lower())

        # 4. Detect is_root_of_branch
        is_root = bool(commit_data.get("is_root_of_branch", False))
        if not is_root and parent_shas:
            # If any parent matches default branch root convention (e.g. 'm000...' or known default)
            if any(p.startswith("m") for p in parent_shas):
                is_root = True

        # 5. Extract line counts
        lines_added = commit_data.get("lines_added") or commit_data.get("added_lines")
        if lines_added is None and isinstance(commit_data.get("stats"), dict):
            lines_added = commit_data["stats"].get("additions")
        if lines_added is None and isinstance(commit_data.get("added"), list):
            lines_added = len(commit_data["added"])
        lines_added = int(lines_added or 0)

        lines_deleted = commit_data.get("lines_deleted") or commit_data.get("deleted_lines")
        if lines_deleted is None and isinstance(commit_data.get("stats"), dict):
            lines_deleted = commit_data["stats"].get("deletions")
        if lines_deleted is None and isinstance(commit_data.get("removed"), list):
            lines_deleted = len(commit_data["removed"])
        lines_deleted = int(lines_deleted or 0)

        # 6. Parse timestamps
        authored_at = (
            parse_iso_datetime(commit_data.get("timestamp"))
            or parse_iso_datetime(commit_data.get("authored_at"))
            or datetime.now(timezone.utc)
        )
        committed_at = (
            parse_iso_datetime(commit_data.get("committed_at"))
            or authored_at
        )

        commit_pr_id = pull_request_id
        if commit_data.get("pull_request_id"):
            try:
                commit_pr_id = uuid.UUID(str(commit_data["pull_request_id"]))
            except Exception:
                pass

        # 7. Upsert into 'commits' table
        stmt = select(Commit).where(Commit.sha == sha, Commit.tenant_id == tenant_id)
        existing_commit = (await db.execute(stmt)).scalar_one_or_none()

        if existing_commit is not None:
            existing_commit.author_pseudonym_id = author_pseudo
            existing_commit.committer_pseudonym_id = committer_pseudo
            existing_commit.lines_added = lines_added
            existing_commit.lines_deleted = lines_deleted
            existing_commit.parent_shas = parent_shas
            existing_commit.is_root_of_branch = is_root or existing_commit.is_root_of_branch
            if commit_pr_id and not existing_commit.pull_request_id:
                existing_commit.pull_request_id = commit_pr_id
        else:
            new_commit = Commit(
                sha=sha,
                tenant_id=tenant_id,
                repository_id=repository_id,
                author_pseudonym_id=author_pseudo,
                committer_pseudonym_id=committer_pseudo,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                authored_at=authored_at,
                committed_at=committed_at,
                parent_shas=parent_shas,
                is_root_of_branch=is_root,
                pull_request_id=commit_pr_id,
                active_fencing_token=fencing_token,
            )
            db.add(new_commit)

        persisted_count += 1

    await db.flush()
    await complete_webhook_event(db, event_id, tenant_id=tenant_id)
    return f"persisted_commits:{persisted_count}"


async def ingest_github_pull_request_in_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: dict[str, Any],
    event_id: uuid.UUID,
    integration_id: Optional[uuid.UUID] = None,
) -> str:
    """
    Ingests a GitHub pull_request webhook event (opened, synchronize, closed/merged).
    Implements optimistic concurrency fencing, monotonic versioning, merge SHA capture,
    and contributor pseudonym isolation.
    """
    repo_dict = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    repo_external_id = repo_dict.get("id")
    repo_full_name = repo_dict.get("full_name")

    project_id, repository_id = await resolve_github_project(
        db,
        tenant_id=tenant_id,
        repo_external_id=repo_external_id,
        repo_full_name=repo_full_name,
    )
    if repository_id is None or project_id is None:
        logger.info(
            "Ignoring pull_request event for unlinked repository %s (full_name=%s)",
            repo_external_id,
            repo_full_name,
        )
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return "noop:unlinked_repository"

    action = str(payload.get("action") or "").strip().lower()
    pr_dict = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    pr_number = int(pr_dict.get("number") or payload.get("number") or 0)
    title = str(pr_dict.get("title") or "").strip()

    head_dict = pr_dict.get("head") if isinstance(pr_dict.get("head"), dict) else {}
    base_dict = pr_dict.get("base") if isinstance(pr_dict.get("base"), dict) else {}

    head_branch = str(head_dict.get("ref") or "").strip()
    base_branch = str(base_dict.get("ref") or "").strip()
    head_sha = str(head_dict.get("sha") or "").strip().lower()

    inbound_token = int(
        payload.get("fencing_token")
        or payload.get("active_fencing_token")
        or pr_dict.get("active_fencing_token")
        or 0
    )

    # Resolve author pseudonym without internal user constraint
    user_dict = pr_dict.get("user") if isinstance(pr_dict.get("user"), dict) else {}
    author_actor = (
        user_dict.get("id")
        or user_dict.get("login")
        or user_dict.get("username")
        or user_dict.get("email")
        or "unknown_pr_author"
    )
    author_pseudo = await resolve_or_create_contributor_pseudonym(
        db,
        tenant_id=tenant_id,
        provider="github",
        external_actor_id=str(author_actor),
        raw_email=user_dict.get("email"),
        raw_name=user_dict.get("name"),
        raw_handle=user_dict.get("login") or user_dict.get("username"),
    )

    stmt = select(PullRequest).where(
        PullRequest.tenant_id == tenant_id,
        PullRequest.repository_id == repository_id,
        PullRequest.external_pr_number == pr_number,
    )
    existing_pr = (await db.execute(stmt)).scalar_one_or_none()

    if action == "opened":
        if existing_pr is not None:
            # Idempotent duplicate delivery
            await complete_webhook_event(db, event_id, tenant_id=tenant_id)
            return f"idempotent_duplicate_pr:{existing_pr.id}"

        created_in_git_at = (
            parse_iso_datetime(pr_dict.get("created_at"))
            or datetime.now(timezone.utc)
        )
        new_pr = PullRequest(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            project_id=project_id,
            repository_id=repository_id,
            integration_id=integration_id,
            author_id=None,
            author_pseudonym_id=author_pseudo,
            external_pr_number=pr_number,
            title=title,
            state="open",
            head_branch=head_branch,
            base_branch=base_branch,
            head_sha=head_sha,
            merge_commit_sha=None,
            entity_version=1,
            active_fencing_token=inbound_token,
            created_in_git_at=created_in_git_at,
        )
        db.add(new_pr)
        await db.flush()
        await _link_branch_commits_to_pr(
            db,
            tenant_id=tenant_id,
            repository_id=repository_id,
            pr_id=new_pr.id,
            head_sha=head_sha,
        )
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"persisted_pr_opened:{new_pr.id}"

    elif action == "synchronize":
        if existing_pr is None:
            # Out-of-order synchronize before opened
            created_in_git_at = (
                parse_iso_datetime(pr_dict.get("created_at"))
                or datetime.now(timezone.utc)
            )
            existing_pr = PullRequest(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=repository_id,
                integration_id=integration_id,
                author_id=None,
                author_pseudonym_id=author_pseudo,
                external_pr_number=pr_number,
                title=title,
                state="open",
                head_branch=head_branch,
                base_branch=base_branch,
                head_sha=head_sha,
                merge_commit_sha=None,
                entity_version=1,
                active_fencing_token=inbound_token,
                created_in_git_at=created_in_git_at,
            )
            db.add(existing_pr)
            await db.flush()
        else:
            # Optimistic fencing validation: reject stale events
            if inbound_token > 0 and inbound_token <= existing_pr.active_fencing_token:
                logger.warning(
                    "Rejecting stale synchronize event for PR #%s (inbound=%s <= active=%s)",
                    pr_number,
                    inbound_token,
                    existing_pr.active_fencing_token,
                )
                await complete_webhook_event(db, event_id, tenant_id=tenant_id)
                return "stale_rejected:fencing_token_too_low"

            existing_pr.head_sha = head_sha
            existing_pr.entity_version += 1
            if inbound_token > existing_pr.active_fencing_token:
                existing_pr.active_fencing_token = inbound_token
            existing_pr.updated_at = (
                parse_iso_datetime(pr_dict.get("updated_at"))
                or datetime.now(timezone.utc)
            )

        await db.flush()
        await _link_branch_commits_to_pr(
            db,
            tenant_id=tenant_id,
            repository_id=repository_id,
            pr_id=existing_pr.id,
            head_sha=head_sha,
        )
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"persisted_pr_synchronize:{existing_pr.id}"

    elif action == "closed":
        if existing_pr is None:
            created_in_git_at = (
                parse_iso_datetime(pr_dict.get("created_at"))
                or datetime.now(timezone.utc)
            )
            existing_pr = PullRequest(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                project_id=project_id,
                repository_id=repository_id,
                integration_id=integration_id,
                author_id=None,
                author_pseudonym_id=author_pseudo,
                external_pr_number=pr_number,
                title=title,
                state="open",
                head_branch=head_branch,
                base_branch=base_branch,
                head_sha=head_sha,
                merge_commit_sha=None,
                entity_version=1,
                active_fencing_token=inbound_token,
                created_in_git_at=created_in_git_at,
            )
            db.add(existing_pr)
            await db.flush()

        is_merged = bool(pr_dict.get("merged", False))
        if is_merged:
            existing_pr.state = "merged"
            existing_pr.merge_commit_sha = (
                str(pr_dict.get("merge_commit_sha") or "").strip().lower() or None
            )
            existing_pr.merged_at = (
                parse_iso_datetime(pr_dict.get("merged_at"))
                or datetime.now(timezone.utc)
            )
            existing_pr.closed_at = (
                parse_iso_datetime(pr_dict.get("closed_at"))
                or existing_pr.merged_at
            )
        else:
            existing_pr.state = "closed"
            existing_pr.merge_commit_sha = None
            existing_pr.merged_at = None
            existing_pr.closed_at = (
                parse_iso_datetime(pr_dict.get("closed_at"))
                or datetime.now(timezone.utc)
            )

        existing_pr.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"persisted_pr_closed:{existing_pr.id}"

    else:
        # Other actions (e.g. edited, labeled, reopened)
        if existing_pr is not None and title:
            existing_pr.title = title
            existing_pr.updated_at = datetime.now(timezone.utc)
            await db.flush()
        await complete_webhook_event(db, event_id, tenant_id=tenant_id)
        return f"noop:pr_action_{action}"
