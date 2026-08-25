"""Point this PR's Vercel preview at this PR's LangSmith preview deployment.

    uv run python scripts/wire_vercel_preview.py

WHY THIS EXISTS. Both platforms build a preview per pull request, but neither knows about
the other, and the LangSmith preview's URL contains a random hash so it cannot be computed -
only looked up. Without this, a Vercel preview talks to whatever `VITE_LG_URL` the project
has, which is the production deployment: you review a PR's UI against main's backend.

WHY IT RUNS IN CI RATHER THAN IN THE VERCEL BUILD. Both platforms start building the moment
you push, and the LangSmith build takes minutes, so at Vercel-build time the preview usually
does not exist yet. From CI we can WAIT for it, set the variable, and then trigger a
redeploy - which is the only way the first commit on a PR gets a correctly wired preview.
`VITE_LG_URL` is baked in at build time, so setting the variable without redeploying changes
nothing.

Env it needs (all from GitHub Actions):
    LS_CROSS_WORKSPACE_KEY, LANGSMITH_TENANT_ID   the LangSmith lookup
    VERCEL_TOKEN, VERCEL_PROJECT_ID               the Vercel write (+ VERCEL_TEAM_ID if the
                                                  project lives in a team)
    GITHUB_HEAD_REF                               the PR's branch, set by Actions

Absent a Vercel token it exits 0 with an explanation rather than failing: a fork's PR cannot
have those secrets, and that should not read as a broken build.
"""

from __future__ import annotations

import os
import sys
import time

import httpx

CONTROL_PLANE = "https://api.host.langchain.com"
VERCEL = "https://api.vercel.com"
ENV_KEY = "VITE_LG_URL"
# The LangSmith build is minutes of work; this is the ceiling before we give up and say so.
WAIT_SECONDS = 20 * 60
POLL_SECONDS = 20


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def find_langsmith_preview(branch: str, key: str, tenant: str) -> str:
    """The URL of the LangSmith deployment built from `branch`. Waits for it to be ready.

    Matches on `source_revision_config.repo_ref` rather than on the deployment's name: the
    name carries the PR number and a hash (`CustomDemos-pr-98-c6eae4`), while the ref is
    exactly the thing we know.
    """
    headers = {"X-Api-Key": key, "X-Tenant-Id": tenant}
    deadline = time.time() + WAIT_SECONDS
    while True:
        r = httpx.get(
            f"{CONTROL_PLANE}/v2/deployments", headers=headers, params={"limit": 100}, timeout=30
        )
        r.raise_for_status()
        for d in r.json().get("resources") or []:
            ref = (d.get("source_revision_config") or {}).get("repo_ref")
            if ref == branch and d.get("url"):
                print(f"found LangSmith preview for {branch}: {d['url']} ({d.get('status')})")
                return str(d["url"])
        if time.time() > deadline:
            _die(
                f"no LangSmith deployment for branch {branch!r} after "
                f"{WAIT_SECONDS // 60} minutes. Are preview builds enabled on the parent "
                "deployment, and does its trigger mode cover this PR?"
            )
        print(f"waiting for the LangSmith preview of {branch}…")
        time.sleep(POLL_SECONDS)


def _vercel(method: str, path: str, token: str, team: str, **kw) -> httpx.Response:
    params = dict(kw.pop("params", {}))
    if team:
        params["teamId"] = team
    return httpx.request(
        method,
        f"{VERCEL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
        **kw,
    )


def set_branch_env(url: str, branch: str, token: str, project: str, team: str) -> None:
    """Upsert `VITE_LG_URL` for this branch's preview environment.

    Vercel has no upsert, so an existing branch-scoped value is deleted first - otherwise the
    create returns a conflict and the preview keeps the old URL.
    """
    existing = _vercel(
        "GET", f"/v9/projects/{project}/env", token, team, params={"decrypt": "false"}
    )
    existing.raise_for_status()
    for env in existing.json().get("envs") or []:
        if env.get("key") == ENV_KEY and env.get("gitBranch") == branch:
            d = _vercel("DELETE", f"/v9/projects/{project}/env/{env['id']}", token, team)
            print(f"removed the previous {ENV_KEY} for {branch} ({d.status_code})")

    created = _vercel(
        "POST",
        f"/v10/projects/{project}/env",
        token,
        team,
        json={
            "key": ENV_KEY,
            "value": url,
            "type": "plain",  # baked into a client bundle at build time; not a secret
            "target": ["preview"],
            "gitBranch": branch,
        },
    )
    if created.status_code >= 400:
        _die(f"could not set {ENV_KEY} for {branch}: {created.status_code} {created.text[:300]}")
    print(f"set {ENV_KEY} for branch {branch}")


def redeploy(branch: str, token: str, project: str, team: str) -> None:
    """Rebuild the branch's latest preview, so the new variable is actually baked in.

    Best-effort: the variable is set either way, and the next commit would pick it up. A
    failure here is worth a warning rather than a red build.
    """
    listing = _vercel(
        "GET",
        "/v6/deployments",
        token,
        team,
        params={"projectId": project, "target": "preview", "limit": 20},
    )
    if listing.status_code >= 400:
        print(f"warning: could not list deployments ({listing.status_code}); skipping redeploy")
        return
    for d in listing.json().get("deployments") or []:
        if (d.get("meta") or {}).get("githubCommitRef") == branch:
            again = _vercel(
                "POST",
                "/v13/deployments",
                token,
                team,
                json={"name": d.get("name"), "deploymentId": d.get("uid"), "target": "preview"},
            )
            print(
                f"redeploy requested for {branch}: {again.status_code}"
                + ("" if again.status_code < 400 else f" {again.text[:200]}")
            )
            return
    print(f"warning: no existing preview deployment for {branch}; the next push will pick it up")


def main() -> int:
    """Resolve the branch, find its LangSmith preview, wire Vercel, redeploy."""
    branch = os.getenv("GITHUB_HEAD_REF") or os.getenv("BRANCH") or ""
    if not branch:
        _die("no branch: expected GITHUB_HEAD_REF (a pull_request event) or BRANCH")

    token = os.getenv("VERCEL_TOKEN", "")
    project = os.getenv("VERCEL_PROJECT_ID", "")
    if not (token and project):
        # A fork's pull request has no access to these, and that is not a failure.
        print("skipping: VERCEL_TOKEN / VERCEL_PROJECT_ID are not set")
        return 0

    key = os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""
    tenant = os.getenv("LANGSMITH_TENANT_ID", "")
    if not (key and tenant):
        _die("LS_CROSS_WORKSPACE_KEY and LANGSMITH_TENANT_ID are required for the lookup")

    url = find_langsmith_preview(branch, key, tenant)
    team = os.getenv("VERCEL_TEAM_ID", "")
    set_branch_env(url, branch, token, project, team)
    redeploy(branch, token, project, team)
    return 0


if __name__ == "__main__":
    sys.exit(main())
