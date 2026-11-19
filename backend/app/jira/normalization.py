"""Normalization of Jira issue / sprint webhook payloads (Task 67.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.webhooks.versioning import extract_jira_issue_version

# Instance-specific custom fields: adjust to your Jira.
STORY_POINTS_FIELDS = ("customfield_10016", "customfield_10028", "story_points")
SPRINT_FIELDS = ("customfield_10020", "sprint")
AC_FIELDS: tuple[str, ...] = ()  # e.g. ("customfield_10100",) if AC is a dedicated field

_AC_HEADING = re.compile(r"acceptance\s+criteria", re.I)
_ANY_HEADING = re.compile(r"^\s*(h[1-6]\.|#{1,6}\s)")
_TZ_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


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


def _ms_to_dt(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class NormalizedSprint:
    external_id: str
    name: Optional[str]
    state: Optional[str]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    complete_date: Optional[datetime] = None
    goal: Optional[str] = None


@dataclass
class NormalizedIssue:
    key: str
    external_id: Optional[str]
    issue_type: Optional[str]
    title: Optional[str]
    status: Optional[str]
    story_points: Optional[float]
    sprint: Optional[NormalizedSprint]
    links: list[dict[str, Any]]
    assignee: Optional[dict[str, Optional[str]]]
    changelog_id: Optional[str]
    status_change: Optional[tuple[Optional[str], Optional[str]]]
    ac_text: Optional[str]
    created_at: Optional[datetime]
    event_at: Optional[datetime]
    incoming_version: Optional[int] = None


@dataclass
class NormalizedJiraEvent:
    kind: str  # "issue" | "sprint" | "noop"
    reason: Optional[str] = None
    event_name: Optional[str] = None
    issue: Optional[NormalizedIssue] = None
    sprint: Optional[NormalizedSprint] = None


def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format (or plain string) to text; headings get '# '."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    inner = adf_to_text(node.get("content"))
    if t == "heading":
        return f"\n# {inner}\n"
    if t in {"paragraph", "listItem", "codeBlock", "blockquote", "tableRow"}:
        return inner + "\n"
    return inner


def extract_acceptance_criteria(fields: dict[str, Any]) -> Optional[str]:
    for name in AC_FIELDS:
        if name in fields:
            return adf_to_text(fields.get(name)).strip()
    desc = fields.get("description")
    if desc is None:
        return None
    lines = adf_to_text(desc).splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if not capturing:
            if _AC_HEADING.search(line):
                capturing = True
                # text after "Acceptance Criteria:" on the same line
                tail = _AC_HEADING.split(line, maxsplit=1)[-1].lstrip(" :*_#.-")
                if tail and not _ANY_HEADING.match(line):
                    out.append(tail)
            continue
        if _ANY_HEADING.match(line):
            break
        out.append(line)
    text = "\n".join(out).strip()
    return text if capturing else ""


def _story_points(fields: dict[str, Any]) -> Optional[float]:
    for name in STORY_POINTS_FIELDS:
        v = fields.get(name)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def _sprint_from(raw: Any) -> Optional[NormalizedSprint]:
    if not isinstance(raw, dict) or raw.get("id") is None:
        return None
    return NormalizedSprint(
        external_id=str(raw["id"]),
        name=raw.get("name"),
        state=(raw.get("state") or "").lower() or None,
        start_date=parse_dt(raw.get("startDate")),
        end_date=parse_dt(raw.get("endDate")),
        complete_date=parse_dt(raw.get("completeDate")),
        goal=raw.get("goal"),
    )


def _issue_sprint(fields: dict[str, Any]) -> Optional[NormalizedSprint]:
    for name in SPRINT_FIELDS:
        raw = fields.get(name)
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        sprints = [s for s in (_sprint_from(i) for i in items) if s]
        if not sprints:
            continue
        active = [s for s in sprints if s.state == "active"]
        return (active or sprints)[-1]
    return None


def _links(fields: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for link in fields.get("issuelinks") or []:
        ltype = (link.get("type") or {}).get("name")
        for direction in ("inwardIssue", "outwardIssue"):
            other = link.get(direction)
            if other and other.get("key"):
                out.append(
                    {
                        "type": ltype,
                        "direction": "inward" if direction == "inwardIssue" else "outward",
                        "key": other["key"],
                    }
                )
    return out


def normalize_jira_event(
    event_type: Optional[str],
    payload: dict[str, Any],
    *,
    received_at: Optional[datetime] = None,
) -> NormalizedJiraEvent:
    name = payload.get("webhookEvent") or event_type or ""
    if name.startswith("sprint_"):
        sprint = _sprint_from(payload.get("sprint"))
        if sprint is None:
            return NormalizedJiraEvent(kind="noop", reason="missing_sprint")
        return NormalizedJiraEvent(kind="sprint", event_name=name, sprint=sprint)

    if name not in {"jira:issue_created", "jira:issue_updated"}:
        return NormalizedJiraEvent(kind="noop", reason=f"unsupported_event:{name}")

    raw = payload.get("issue") or {}
    key = raw.get("key")
    if not key:
        return NormalizedJiraEvent(kind="noop", reason="missing_issue_key")
    fields = raw.get("fields") or {}

    assignee_raw = fields.get("assignee")
    assignee = None
    if isinstance(assignee_raw, dict):
        assignee = {
            "id": assignee_raw.get("accountId")
            or assignee_raw.get("name")
            or assignee_raw.get("key"),
            "name": assignee_raw.get("displayName"),
            "email": assignee_raw.get("emailAddress"),
        }

    changelog = payload.get("changelog") or {}
    status_change = None
    for item in changelog.get("items") or []:
        if item.get("field") == "status":
            status_change = (item.get("fromString"), item.get("toString"))

    cl_id = changelog.get("id")
    event_at = _ms_to_dt(payload.get("timestamp")) or parse_dt(fields.get("updated")) or received_at
    issue = NormalizedIssue(
        key=key,
        external_id=str(raw["id"]) if raw.get("id") is not None else None,
        issue_type=(fields.get("issuetype") or {}).get("name"),
        title=fields.get("summary"),
        status=(fields.get("status") or {}).get("name"),
        story_points=_story_points(fields),
        sprint=_issue_sprint(fields),
        links=_links(fields),
        assignee=assignee,
        changelog_id=str(cl_id) if cl_id is not None else None,
        status_change=status_change,
        ac_text=extract_acceptance_criteria(fields),
        created_at=parse_dt(fields.get("created")),
        event_at=event_at,
        incoming_version=extract_jira_issue_version(payload),
    )
    return NormalizedJiraEvent(kind="issue", event_name=name, issue=issue)
