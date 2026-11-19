"""
Тест вебхуков MANTA: отправляет тестовые события GitHub и Jira и ничего не меняет в коде.
Нужен только Python (стандартная библиотека). Запуск см. в инструкции в чате.
"""
import argparse
import hashlib
import hmac
import json
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:8000")
p.add_argument("--tenant", required=True, help="tenant_id (UUID)")
p.add_argument("--repo-id", required=True, type=int, help="repositories.external_repo_id")
p.add_argument("--repo-name", required=True, help="repositories.full_name, например owner/repo")
p.add_argument("--jira-integration", required=True, help="id интеграции Jira (UUID)")
p.add_argument("--jira-secret", default=None, help="секрет Jira, если он задан у интеграции")
args = p.parse_args()

RUN = secrets.token_hex(3)
now = datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def send(path, payload, headers):
    body = json.dumps(payload).encode()
    h = {"Content-Type": "application/json", **headers}
    if args.jira_secret and "/integrations/" in path:
        h["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            args.jira_secret.encode(), body, hashlib.sha256
        ).hexdigest()
    req = urllib.request.Request(args.url + path, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def gh(event, payload):
    return send(
        "/api/v1/webhooks/github",
        payload,
        {
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Tenant-ID": args.tenant,
        },
    )


_ts = [int(now.timestamp() * 1000)]


def jira(payload):
    _ts[0] += 1000
    payload["timestamp"] = _ts[0]
    return send(
        f"/api/v1/webhooks/integrations/{args.jira_integration}",
        payload,
        {
            "X-Atlassian-Webhook-Event": payload["webhookEvent"],
            "X-Atlassian-Webhook-Identifier": str(uuid.uuid4()),
        },
    )


def show(name, res):
    ok = "OK " if res[0] == 202 else "ERR"
    print(f"[{ok}] {name}: HTTP {res[0]} {res[1][:160]}")


repo = {"id": args.repo_id, "full_name": args.repo_name}
sha1, sha2, merge_sha = (secrets.token_hex(20) for _ in range(3))
branch = f"feature/test-{RUN}"
t1, t2 = now - timedelta(hours=5), now - timedelta(hours=4)
author = {"name": "Test Dev", "email": f"dev-{RUN}@example.com", "username": f"dev{RUN}"}

print(f"Тестовый прогон {RUN}\n")

# ---------- GitHub ----------
show(
    "GitHub push (2 коммита, новая ветка)",
    gh(
        "push",
        {
            "ref": f"refs/heads/{branch}",
            "created": True,
            "deleted": False,
            "before": "0" * 40,
            "after": sha2,
            "repository": repo,
            "commits": [
                {"id": sha1, "timestamp": iso(t1), "author": author, "committer": author},
                {"id": sha2, "timestamp": iso(t2), "author": author, "committer": author},
            ],
        },
    ),
)

pr_base = {
    "number": int(RUN, 16) % 100000 + 1000,
    "title": f"Test PR {RUN}",
    "user": {"login": author["username"]},
    "created_at": iso(t2 + timedelta(minutes=5)),
    "head": {"ref": branch, "sha": sha2},
    "base": {"ref": "main", "sha": secrets.token_hex(20)},
}
show(
    "GitHub pull_request opened",
    gh(
        "pull_request",
        {"action": "opened", "number": pr_base["number"], "repository": repo,
         "pull_request": {**pr_base, "state": "open", "merged": False}},
    ),
)
show(
    "GitHub pull_request closed+merged",
    gh(
        "pull_request",
        {"action": "closed", "number": pr_base["number"], "repository": repo,
         "pull_request": {**pr_base, "state": "closed", "merged": True,
                          "merged_at": iso(now), "closed_at": iso(now),
                          "merge_commit_sha": merge_sha}},
    ),
)

# ---------- Jira ----------
key = f"TEST-{int(RUN, 16) % 90000 + 1000}"
sprint = {"id": 4000 + int(RUN, 16) % 1000, "name": f"Sprint {RUN}", "state": "active",
          "startDate": iso(now - timedelta(days=3)), "endDate": iso(now + timedelta(days=7))}


def issue_payload(event, issue_id, status, desc, issue_type="Story", changelog=None, created=None):
    pl = {
        "webhookEvent": event,
        "issue": {
            "id": str(issue_id),
            "key": key if issue_type == "Story" else f"{key}-INC",
            "fields": {
                "issuetype": {"name": issue_type},
                "summary": f"Тест {issue_type} {RUN}",
                "status": {"name": status},
                "customfield_10016": 5,
                "customfield_10020": [sprint],
                "assignee": {"accountId": f"acc-{RUN}", "displayName": "Jira Tester",
                             "emailAddress": f"jira-{RUN}@example.com"},
                "description": desc,
                "created": iso(created or now - timedelta(hours=2)),
                "updated": iso(now),
                "issuelinks": [],
            },
        },
    }
    if changelog:
        pl["changelog"] = changelog
    return pl


desc1 = "Описание\n\nAcceptance Criteria:\n- Пользователь может войти\n"
desc2 = "Описание\n\nAcceptance Criteria:\n- Пользователь может войти\n- Есть сброс пароля\n"

show("Jira sprint_started", jira({"webhookEvent": "sprint_started", "sprint": sprint}))
show("Jira issue_created (Story)", jira(issue_payload("jira:issue_created", 90000 + int(RUN, 16) % 9999, "To Do", desc1)))
base_id = 90000 + int(RUN, 16) % 9999
cl = int(RUN, 16) % 50000 + 100
show(
    "Jira issue_updated (To Do -> In Progress, AC изменён)",
    jira(issue_payload("jira:issue_updated", base_id, "In Progress", desc2,
                       changelog={"id": str(cl), "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]})),
)
show(
    "Jira issue_updated повтор того же changelog (должен игнорироваться)",
    jira(issue_payload("jira:issue_updated", base_id, "In Progress", desc2,
                       changelog={"id": str(cl), "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]})),
)

inc_id = base_id + 1
show("Jira issue_created (Incident)", jira(issue_payload("jira:issue_created", inc_id, "Open", "Сбой в проде", issue_type="Incident", created=now - timedelta(minutes=10))))
show(
    "Jira Incident -> Cancelled",
    jira(issue_payload("jira:issue_updated", inc_id, "Cancelled", "Сбой в проде", issue_type="Incident",
                       changelog={"id": str(cl + 1), "items": [{"field": "status", "fromString": "Open", "toString": "Cancelled"}]})),
)

print(f"\nГотово. Подожди 10-20 секунд и проверь базу. Метки этого прогона: ветка {branch}, задача {key}, PR #{pr_base['number']}")
sys.exit(0)
