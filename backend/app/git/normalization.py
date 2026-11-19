"""Normalization of GitHub push / pull_request payloads (Task 20.4)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.webhooks.versioning import extract_github_pr_version

_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")
HANDLED_PR_ACTIONS = {"opened", "synchronize", "reopened", "closed"}


def parse_dt(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    else:
        s = _TZ_NO_COLON.sub(r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


ZERO_SHA = "0" * 40


@dataclass
class NormalizedCommit:
    sha: str
    authored_at: Optional[datetime]
    committed_at: Optional[datetime]
    author_login: Optional[str]
    author_name: Optional[str]
    author_email: Optional[str]
    committer_login: Optional[str] = None
    committer_name: Optional[str] = None
    committer_email: Optional[str] = None
    parent_shas: list[str] = field(default_factory=list)
    is_root_of_branch: bool = False


@dataclass
class NormalizedPush:
    kind: str  # "push" | "noop"
    reason: Optional[str] = None
    repo_external_id: Optional[str] = None
    repo_full_name: Optional[str] = None
    branch: Optional[str] = None
    commits: list[NormalizedCommit] = field(default_factory=list)


@dataclass
class NormalizedPullRequest:
    kind: str  # "pull_request" | "noop"
    reason: Optional[str] = None
    action: Optional[str] = None
    repo_external_id: Optional[str] = None
    repo_full_name: Optional[str] = None
    number: Optional[int] = None
    title: Optional[str] = None
    state: Optional[str] = None  # OPEN | MERGED | CLOSED
    head_branch: Optional[str] = None
    base_branch: Optional[str] = None
    head_sha: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    author_login: Optional[str] = None
    incoming_version: Optional[int] = None


def _repo(payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    repo = payload.get("repository") or {}
    rid = repo.get("id")
    return (str(rid) if rid is not None else None), repo.get("full_name")


def normalize_push(payload: dict[str, Any]) -> NormalizedPush:
    ref = payload.get("ref") or ""
    if not ref.startswith("refs/heads/"):
        return NormalizedPush(kind="noop", reason="non_branch_ref")
    if payload.get("deleted"):
        return NormalizedPush(kind="noop", reason="branch_deleted")
    repo_id, full_name = _repo(payload)
    if repo_id is None and not full_name:
        return NormalizedPush(kind="noop", reason="missing_repository")

    created = bool(payload.get("created"))
    before = payload.get("before") or ""
    # parent of the first commit in the push (unknown for a brand-new branch)
    prev: Optional[str] = None if (created or not before or before == ZERO_SHA) else before

    commits: list[NormalizedCommit] = []
    for idx, c in enumerate(payload.get("commits") or []):
        sha = c.get("id") or c.get("sha")
        if not sha:
            continue
        ts = parse_dt(c.get("timestamp"))
        author = c.get("author") or {}
        committer = c.get("committer") or {}

        if isinstance(c.get("parents"), list):
            parents = [p if isinstance(p, str) else (p or {}).get("sha") for p in c["parents"]]
            parents = [p for p in parents if p]
            is_root = len(parents) == 0
        else:
            # push payload has no parents: assume linear order (oldest first)
            parents = [prev] if prev else []
            is_root = created and idx == 0
        prev = sha

        commits.append(
            NormalizedCommit(
                sha=sha,
                authored_at=ts,
                committed_at=ts,
                author_login=author.get("username"),
                author_name=author.get("name"),
                author_email=author.get("email"),
                committer_login=committer.get("username"),
                committer_name=committer.get("name"),
                committer_email=committer.get("email"),
                parent_shas=parents,
                is_root_of_branch=is_root,
            )
        )
    if not commits:
        return NormalizedPush(kind="noop", reason="no_commits")
    return NormalizedPush(
        kind="push",
        repo_external_id=repo_id,
        repo_full_name=full_name,
        branch=ref[len("refs/heads/") :],
        commits=commits,
    )


def normalize_pull_request(payload: dict[str, Any]) -> NormalizedPullRequest:
    action = payload.get("action")
    if action not in HANDLED_PR_ACTIONS:
        return NormalizedPullRequest(kind="noop", reason=f"unhandled_action:{action}")
    pr = payload.get("pull_request") or {}
    number = pr.get("number") or payload.get("number")
    if number is None:
        return NormalizedPullRequest(kind="noop", reason="missing_pr_number")
    repo_id, full_name = _repo(payload)
    if repo_id is None and not full_name:
        return NormalizedPullRequest(kind="noop", reason="missing_repository")

    if pr.get("merged"):
        state = "MERGED"
    elif pr.get("state") == "closed":
        state = "CLOSED"
    else:
        state = "OPEN"

    head = pr.get("head") or {}
    base = pr.get("base") or {}
    return NormalizedPullRequest(
        kind="pull_request",
        action=action,
        repo_external_id=repo_id,
        repo_full_name=full_name,
        number=int(number),
        title=pr.get("title"),
        state=state,
        head_branch=head.get("ref"),
        base_branch=base.get("ref"),
        head_sha=head.get("sha"),
        merge_commit_sha=pr.get("merge_commit_sha") if state == "MERGED" else None,
        created_at=parse_dt(pr.get("created_at")),
        updated_at=parse_dt(pr.get("updated_at")),
        merged_at=parse_dt(pr.get("merged_at")),
        closed_at=parse_dt(pr.get("closed_at")),
        author_login=(pr.get("user") or {}).get("login"),
        incoming_version=extract_github_pr_version(payload),
    )
