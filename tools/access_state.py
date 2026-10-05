"""Materialize current access state from complete GitHub-authenticated approval history."""
from __future__ import annotations
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "chaonpu/ziwuxilv"
ADMIN_ID = 205125766
REQUEST = "<!-- ziwuxilv-native-access:request:v1 -->"
DECISION = "<!-- ziwuxilv-native-access:decision:v1:"
GRANT = "<!-- ziwuxilv-native-access:grant:v2:"
INVITATION = "<!-- ziwuxilv-native-access:invite:v1:"

def milliseconds(value):
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError, AttributeError):
        return 0

def is_request(issue):
    return (not issue.get("pull_request") and isinstance(issue.get("body"), str)
        and issue["body"].startswith(REQUEST) and type(issue.get("number")) is int
        and issue["number"] > 0 and type(issue.get("user", {}).get("id")) is int
        and issue["user"]["id"] > 0)

def materialize(issues, comments_by_issue, now):
    histories = {}
    for issue in issues:
        if not is_request(issue):
            continue
        number, user = issue["number"], issue["user"]
        decision, invite, decision_id, invite_id = None, None, 0, 0
        expected_url = f"https://api.github.com/repos/{REPOSITORY}/issues/{number}"
        for comment in comments_by_issue.get(number, []):
            if comment.get("issue_url") != expected_url:
                raise ValueError("Approval comment belongs to a foreign issue")
            if comment.get("user", {}).get("id") != ADMIN_ID:
                continue
            cid, body = comment.get("id", 0), comment.get("body", "").strip()
            if type(cid) is not int or cid <= 0:
                continue
            at = milliseconds(comment.get("created_at"))
            if body.startswith(GRANT) and body.endswith(" -->") and cid > decision_id:
                decision = dict(status="revoked", updatedAt=at, authorizationRole="member", expiresAt=None)
                try:
                    grant = json.loads(body[len(GRANT):-4])
                    role, days = grant["role"], grant["days"]
                    if (role not in ("registered", "member") or at <= 0
                        or days is not None and (type(days) is not int or not 1 <= days <= 36500)
                        or role == "registered" and days is not None):
                        raise ValueError("Invalid grant")
                    decision.update(status="approved", authorizationRole=role,
                        expiresAt=None if days is None else at + days * 86400000)
                except (KeyError, ValueError, TypeError):
                    pass  # A malformed newer admin grant revokes rather than reviving an older grant.
                decision_id = cid
            elif cid > decision_id and body in [f"{DECISION}{status} -->" for status in ("approved", "denied", "revoked")]:
                status = body[len(DECISION):-4]
                decision = dict(status=status, updatedAt=at, authorizationRole="member", expiresAt=None)
                decision_id = cid
            if body.startswith(INVITATION) and body.endswith(" -->") and cid > invite_id:
                text = body[len(INVITATION):-4].strip()
                if re.fullmatch(r"[0-9]+", text) and int(text) > 0:
                    invite, invite_id = int(text), cid
        row = dict(number=number, userId=user["id"], name=user.get("login", ""),
            message=issue["body"].split("申请说明：\n", 1)[-1][:500] if "申请说明：\n" in issue["body"] else "",
            status="pending", updatedAt=milliseconds(issue.get("created_at")),
            invitationId=invite, authorizationRole="member", expiresAt=None, registrationRetained=False)
        row.update(decision or {})
        histories.setdefault(user["id"], []).append(row)
    records = []
    for history in histories.values():
        previous = None
        for latest in sorted(history, key=lambda row: row["number"]):
            active = (previous is not None and (previous["status"] == "approved"
                or previous["status"] in ("pending", "denied") and previous["registrationRetained"])
                and (previous["expiresAt"] is None or now < previous["expiresAt"]))
            if latest["status"] in ("pending", "denied") and active and previous["authorizationRole"] == "registered":
                latest.update(authorizationRole="registered", registrationRetained=True)
            previous = latest
        records.append(previous)
    return dict(schemaVersion=1, repository=REPOSITORY, adminId=ADMIN_ID, generatedAt=now,
        records=sorted(records, key=lambda row: row["userId"]))

def api(path):
    if not path.startswith(f"/repos/{REPOSITORY}/issues"):
        raise ValueError("Unexpected GitHub API path")
    request = urllib.request.Request("https://api.github.com" + path, headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ZiWuXiLv-Access-State"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

def pages(path):
    output = []
    for page in range(1, 101):
        batch = api(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Incomplete GitHub response")
        output.extend(batch)
        if len(batch) < 100:
            return output
    raise ValueError("Incomplete approval history; retaining previous state")

def generate():
    issues = [row for row in pages(f"/repos/{REPOSITORY}/issues?state=all&sort=created&direction=asc") if is_request(row)]
    comments = {row["number"]: pages(f"/repos/{REPOSITORY}/issues/{row['number']}/comments") for row in issues}
    return materialize(issues, comments, int(datetime.now(timezone.utc).timestamp() * 1000))

def main():
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    for attempt in range(4):
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
        # Re-read live history after retrying a push so a newer revocation never gets overwritten.
        state = generate()
        target = Path("access/current.json"); target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "access/current.json"], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "Refresh verified access state"], check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"]).returncode == 0:
            print("Current access state published", len(state["records"])); return
    raise RuntimeError("Access state publication conflicted; previous state remains active")

if __name__ == "__main__":
    main()
