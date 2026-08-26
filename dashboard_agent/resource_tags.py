"""Tag an assistant's LangSmith resources with an Application.

So a shared workspace stays navigable.

WHY. Every assistant scatters resources across one workspace: a tracing project, an eval
dataset, a judge prompt and evaluator, a Context Hub agent repo and a skills bundle. With
two dozen assistants that is a few hundred objects in flat lists, and nothing says which
demo any of them belongs to. `Application` is the tag key LangSmith creates in every
workspace for exactly this, and the UI can filter by it.

WHAT THIS IS NOT. Resource tags are not run tags and not prompt commit tags. They attach to
the RESOURCE (`POST /taggings` with a `tag_value_id` and a resource id), which is why
everything here has to resolve a name into a UUID first.

BEST-EFFORT BY CONTRACT. Every function returns a receipt and never raises. This runs during
assistant provisioning, alongside the eval and voice trimmings, and none of it may fail a
customer's assistant. Two failures are expected rather than exceptional:

  - Applying a tag needs only update access on the resource, but CREATING a tag value needs
    `workspaces:manage`. A key without it gets a 403 here and the assistant is simply
    untagged. (A personal access token inherits the user's workspace role, so granting this
    is a role change, not a per-key setting.)
  - Resource tags are a Plus/Enterprise feature, so the whole endpoint can 404.
"""

from __future__ import annotations

import re

import httpx

API = "https://api.smith.langchain.com/api/v1"
APPLICATION_KEY = "Application"
TIMEOUT = 20.0

# Resource types this module knows how to tag, in the order the receipt reports them.
# The full server-side set is larger (dashboard, queue, sandbox, ...); these are the ones
# assistant setup actually creates.
RESOURCE_TYPES = ("project", "dataset", "prompt", "agent", "evaluator")


def application_for(customer: str) -> str:
    """The Application value for a customer, e.g. "Acme Co" -> "assistant-acme-co"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (customer or "").lower()).strip("-") or "customer"
    return f"assistant-{slug}"


def _headers(key: str, workspace: str | None) -> dict[str, str]:
    """Auth plus the workspace to act in.

    `X-Tenant-Id` matters: the tag endpoints are all `/workspaces/current`, which without it
    resolves to whatever workspace the KEY belongs to - not the assistant's. Every assistant
    here carries its own `ls_workspace`, so leaving it off would tag the wrong workspace's
    resources, or nothing at all.
    """
    h = {"x-api-key": key, "Content-Type": "application/json"}
    if workspace:
        h["X-Tenant-Id"] = workspace
    return h


def _open(api_key: str, workspace: str | None) -> httpx.Client:
    """The HTTP client every call in this module goes through.

    A named seam so tests can substitute a MockTransport. Patching `httpx.Client` itself
    would recurse, since the replacement has to construct one.
    """
    return httpx.Client(headers=_headers(api_key, workspace), timeout=TIMEOUT)


def _value_id(client: httpx.Client, application: str) -> tuple[str, str]:
    """The tag value id for `application`, creating it if absent. `(id, error)`."""
    r = client.get(f"{API}/workspaces/current/tags")
    if r.status_code >= 300:
        return "", f"could not list tags ({r.status_code})"
    key_id = ""
    for tag in r.json() or []:
        if tag.get("key") != APPLICATION_KEY:
            continue
        key_id = tag.get("id") or ""
        for v in tag.get("values") or []:
            if v.get("value") == application:
                return v.get("id") or "", ""
    if not key_id:
        return "", f"the workspace has no {APPLICATION_KEY!r} tag key"

    made = client.post(
        f"{API}/workspaces/current/tag-keys/{key_id}/tag-values", json={"value": application}
    )
    if made.status_code >= 300:
        # 403 here is the common case and it is a PERMISSION, not a bug: applying a tag
        # needs update access on the resource, but minting a new value needs
        # `workspaces:manage`. Reported so the caller can say so rather than shrug.
        return "", f"could not create the value {application!r} ({made.status_code})"
    return made.json().get("id") or "", ""


def _resolve(client: httpx.Client, kind: str, name: str) -> str:
    """A resource's UUID from its name, or "" if it cannot be found."""
    if kind == "project":
        r = client.get(f"{API}/sessions", params={"name": name, "limit": 1})
        rows = r.json() if r.status_code < 300 else []
        return (rows[0].get("id") or "") if isinstance(rows, list) and rows else ""
    if kind == "dataset":
        r = client.get(f"{API}/datasets", params={"name": name, "limit": 1})
        rows = r.json() if r.status_code < 300 else []
        return (rows[0].get("id") or "") if isinstance(rows, list) and rows else ""
    if kind in ("prompt", "agent"):
        # Both live in the hub `/repos` collection; `repo_type` separates a Prompt Hub
        # prompt from a Context Hub agent repo. `query` narrows server-side - listing and
        # scanning pages would miss anything past the first hundred - and
        # `is_public=false` is what confines the answer to THIS workspace: without it the
        # same handle matches public repos from other tenants, and we would tag a stranger's
        # id. Lower-case "false" because the API rejects Python's "False" with a 422.
        r = client.get(
            f"{API}/repos",
            params={
                "query": name,
                "repo_type": kind,
                "limit": 50,
                "is_public": "false",
                "is_archived": "false",
            },
        )
        if r.status_code >= 300:
            return ""
        for repo in r.json().get("repos") or []:
            if repo.get("repo_handle") == name:
                return repo.get("id") or ""
    return ""


def _apply(
    client: httpx.Client,
    receipt: dict,
    value_id: str,
    wanted: list[tuple[str, str]],
    evaluator_id: str,
) -> dict:
    """Resolve each name and attach the tag. Fills and returns `receipt`."""
    targets: list[tuple[str, str, str]] = []
    for kind, name in wanted:
        rid = _resolve(client, kind, name)
        if rid:
            targets.append((kind, name, rid))
            continue
        if kind == "project":
            # A tracing project does not exist until something traces INTO it, and setup
            # runs before the assistant's first turn - so on a fresh assistant this is the
            # normal case, not an edge one, and the project would stay untagged until
            # someone happened to re-run a backfill. Create it here, pre-tagged in the
            # same call (`tag_value_ids` is applied in the same transaction, so it is
            # never briefly untagged). Harmless if traces arrive later: this is the same
            # project they would have created.
            made = client.post(f"{API}/sessions", json={"name": name, "tag_value_ids": [value_id]})
            if made.status_code < 300:
                receipt["tagged"].append(f"{kind}:{name} (created)")
            else:
                receipt["missing"].append(f"{kind}:{name} ({made.status_code})")
            continue
        # Not an error: a dataset a failure mode never planted does not exist to tag.
        receipt["missing"].append(f"{kind}:{name}")
    if evaluator_id:
        targets.append(("evaluator", evaluator_id, evaluator_id))

    for kind, name, rid in targets:
        r = client.post(
            f"{API}/workspaces/current/taggings",
            json={"tag_value_id": value_id, "resource_type": kind, "resource_id": rid},
        )
        # 409 means it is already tagged, which is success for an idempotent call.
        if r.status_code < 300 or r.status_code == 409:
            receipt["tagged"].append(f"{kind}:{name}")
        else:
            receipt["missing"].append(f"{kind}:{name} ({r.status_code})")
    return receipt


def tag_assistant_resources(
    *,
    api_key: str,
    workspace: str | None,
    customer: str,
    project: str = "",
    dataset: str = "",
    prompts: tuple[str, ...] = (),
    agents: tuple[str, ...] = (),
    evaluator_id: str = "",
    application: str = "",
) -> dict:
    """Tag everything this assistant created with its Application. Never raises.

    Names in, UUIDs resolved here - the caller holds handles, and `/taggings` wants ids.
    `evaluator_id` is passed as an id because setup already has one.

    Returns `{"application", "tagged": [...], "missing": [...], "error": str}`:
    `tagged` is `"<type>:<name>"` per success, `missing` names what could not be resolved
    (a project with no traces yet, for instance, does not exist to tag), and `error` is set
    only when NOTHING could be done - a bad key, no tag key, or no permission to mint the
    value.
    """
    app = application or application_for(customer)
    receipt: dict = {"application": app, "tagged": [], "missing": [], "error": ""}
    if not api_key:
        receipt["error"] = "no LangSmith API key"
        return receipt

    wanted: list[tuple[str, str]] = []
    if project:
        wanted.append(("project", project))
    if dataset:
        wanted.append(("dataset", dataset))
    wanted += [("prompt", p) for p in prompts if p]
    wanted += [("agent", a) for a in agents if a]
    if not wanted and not evaluator_id:
        return receipt

    try:
        with _open(api_key, workspace) as client:
            value_id, err = _value_id(client, app)
            # The two key types want OPPOSITE header shapes, and picking wrong looks
            # identical to having no permission. A cross-workspace SERVICE key needs
            # `X-Tenant-Id` to resolve a workspace at all (403 without it); a PERSONAL
            # access token is already bound to one and is refused when sent someone
            # else's (403 with it). So a 403 on the very first call earns one retry with
            # the header dropped, which covers a local run holding only a PAT.
            if err and err.endswith("(403)") and workspace:
                client.close()
                with _open(api_key, None) as bare:
                    value_id, err = _value_id(bare, app)
                    if not err and value_id:
                        return _apply(bare, receipt, value_id, wanted, evaluator_id)
            if err or not value_id:
                receipt["error"] = err or "no tag value"
                return receipt

            return _apply(client, receipt, value_id, wanted, evaluator_id)
    except Exception as e:  # noqa: BLE001 - provisioning must never fail on a tag
        receipt["error"] = str(e)[:200]
    return receipt
