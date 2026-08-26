"""Unit tests for the Application resource tagging (resource_tags.py).

Every request is faked. The point of these is the CONTRACT: the module must resolve names
to ids, must never raise, and must report a permission failure as a permission failure
rather than as silence - since a key without `workspaces:manage` cannot mint a tag value,
and that is the expected state until someone grants it.
"""

from __future__ import annotations

import httpx
import pytest

from dashboard_agent import resource_tags as rt

APP_KEY_ID = "key-1"
VALUE_ID = "val-1"


def _tags(values: list[str]) -> list[dict]:
    return [
        {
            "key": "Application",
            "id": APP_KEY_ID,
            "values": [
                {"value": v, "id": VALUE_ID if v == values[0] else f"id-{v}"} for v in values
            ],
        },
        {"key": "Environment", "id": "key-2", "values": []},
    ]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


# --- the tag value: found, created, or refused -------------------------------------


def test_an_existing_value_is_reused_rather_than_recreated():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=_tags(["assistant-acme"]))

    with _client(handler) as c:
        assert rt._value_id(c, "assistant-acme") == (VALUE_ID, "")
    # No POST: minting a value needs a permission we should not spend when one exists.
    assert calls == ["GET /api/v1/workspaces/current/tags"]


def test_a_missing_value_is_created():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_tags(["other"]))
        return httpx.Response(200, json={"id": "new-val"})

    with _client(handler) as c:
        assert rt._value_id(c, "assistant-acme") == ("new-val", "")


def test_a_403_on_create_is_reported_as_a_permission_problem():
    """The expected failure: applying a tag needs update access, minting one needs manage."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_tags(["other"]))
        return httpx.Response(403, json={"detail": "Permission denied"})

    with _client(handler) as c:
        value_id, err = rt._value_id(c, "assistant-acme")
    assert value_id == ""
    assert "403" in err and "assistant-acme" in err


def test_a_workspace_without_the_application_key_says_so():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"key": "Environment", "id": "k", "values": []}])

    with _client(handler) as c:
        assert rt._value_id(c, "assistant-acme")[1].startswith("the workspace has no")


# --- name -> id resolution --------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "path"),
    [("project", "/api/v1/sessions"), ("dataset", "/api/v1/datasets")],
)
def test_projects_and_datasets_resolve_by_name(kind, path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        assert request.url.params["name"] == "Acme Co"
        return httpx.Response(200, json=[{"id": "res-1", "name": "Acme Co"}])

    with _client(handler) as c:
        assert rt._resolve(c, kind, "Acme Co") == "res-1"


def test_a_repo_is_scoped_to_this_workspace_and_matched_exactly():
    """`is_public=false` is load-bearing: the same handle exists in other tenants."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "repos": [
                    {"repo_handle": "acme-agents-extra", "id": "wrong"},
                    {"repo_handle": "acme-agents", "id": "right"},
                ]
            },
        )

    with _client(handler) as c:
        assert rt._resolve(c, "agent", "acme-agents") == "right"
    assert seen["is_public"] == "false"
    assert seen["repo_type"] == "agent"
    assert seen["query"] == "acme-agents"


def test_an_unresolvable_name_is_empty_not_an_exception():
    with _client(lambda r: httpx.Response(200, json=[])) as c:
        assert rt._resolve(c, "project", "nope") == ""


# --- the whole pass ---------------------------------------------------------------


def test_the_application_name_is_slugified():
    assert rt.application_for("Acme Co") == "assistant-acme-co"
    assert rt.application_for("") == "assistant-customer"


def test_everything_resolvable_is_tagged_and_the_rest_is_reported(monkeypatch):
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tags"):
            return httpx.Response(200, json=_tags(["assistant-acme-co"]))
        if path.endswith("/taggings"):
            import json

            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "tagging"})
        if path == "/api/v1/sessions":
            return httpx.Response(200, json=[{"id": "proj-1"}])
        if path == "/api/v1/datasets":
            return httpx.Response(200, json=[])  # not created -> reported as missing
        if path == "/api/v1/repos":
            return httpx.Response(
                200, json={"repos": [{"repo_handle": "acme-agent", "id": "ag-1"}]}
            )
        return httpx.Response(404)

    monkeypatch.setattr(rt, "_open", lambda *a, **k: _client(handler))
    out = rt.tag_assistant_resources(
        api_key="k",
        workspace="ws-1",
        customer="Acme Co",
        project="Acme Co",
        dataset="acme-evals",
        agents=("acme-agent",),
        evaluator_id="ev-1",
    )
    assert out["application"] == "assistant-acme-co"
    assert set(out["tagged"]) == {"project:Acme Co", "agent:acme-agent", "evaluator:ev-1"}
    assert out["missing"] == ["dataset:acme-evals"]
    assert out["error"] == ""
    # An evaluator is passed as an id already, so it is never resolved - only tagged.
    assert {p["resource_type"] for p in posted} == {"project", "agent", "evaluator"}
    assert all(p["tag_value_id"] == VALUE_ID for p in posted)


def test_a_project_that_does_not_exist_yet_is_created_pre_tagged(monkeypatch):
    """The normal case on a fresh assistant, not an edge one.

    A tracing project does not exist until something traces into it, and setup runs before
    the first turn - so without this the project stays untagged until someone re-runs a
    backfill, which is exactly what happened to the first assistant created after tagging
    shipped.
    """
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        if path.endswith("/tags"):
            return httpx.Response(200, json=_tags(["assistant-acme-co"]))
        if path == "/api/v1/sessions" and request.method == "GET":
            return httpx.Response(200, json=[])  # no project yet
        if path == "/api/v1/sessions" and request.method == "POST":
            created.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "proj-new"})
        return httpx.Response(404)

    monkeypatch.setattr(rt, "_open", lambda *a, **k: _client(handler))
    out = rt.tag_assistant_resources(
        api_key="k", workspace="ws", customer="Acme Co", project="Acme Co"
    )
    assert out["tagged"] == ["project:Acme Co (created)"]
    assert out["missing"] == [] and out["error"] == ""
    # Pre-tagged in the SAME call, so it is never briefly untagged (matters under ABAC).
    assert created == [{"name": "Acme Co", "tag_value_ids": [VALUE_ID]}]


def test_a_dataset_that_does_not_exist_is_reported_not_created(monkeypatch):
    """Only projects are created.

    A dataset absent because the failure mode planted none must not be conjured into
    existence by a tagging pass.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json=_tags(["assistant-acme-co"]))
        return httpx.Response(200, json=[])

    monkeypatch.setattr(rt, "_open", lambda *a, **k: _client(handler))
    out = rt.tag_assistant_resources(
        api_key="k", workspace="ws", customer="Acme Co", dataset="acme-evals"
    )
    assert out["tagged"] == [] and out["missing"] == ["dataset:acme-evals"]


def test_an_already_tagged_resource_counts_as_tagged(monkeypatch):
    """409 is idempotency, not failure: re-running setup must not report a fake problem."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tags"):
            return httpx.Response(200, json=_tags(["assistant-acme-co"]))
        if request.url.path.endswith("/taggings"):
            return httpx.Response(409, json={"detail": "exists"})
        return httpx.Response(200, json=[{"id": "proj-1"}])

    monkeypatch.setattr(rt, "_open", lambda *a, **k: _client(handler))
    out = rt.tag_assistant_resources(
        api_key="k", workspace="ws", customer="Acme Co", project="Acme Co"
    )
    assert out["tagged"] == ["project:Acme Co"] and out["error"] == ""


def test_a_transport_failure_is_a_receipt_not_a_raise(monkeypatch):
    """This runs during provisioning: it may never fail a customer's assistant."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(rt, "_open", lambda *a, **k: _client(handler))
    out = rt.tag_assistant_resources(api_key="k", workspace="ws", customer="Acme Co", project="p")
    assert out["error"] and out["tagged"] == []


def test_no_key_is_reported_rather_than_attempted():
    out = rt.tag_assistant_resources(api_key="", workspace="ws", customer="Acme", project="p")
    assert out["error"] == "no LangSmith API key"


def test_nothing_to_tag_is_a_no_op():
    out = rt.tag_assistant_resources(api_key="k", workspace="ws", customer="Acme")
    assert out == {"application": "assistant-acme", "tagged": [], "missing": [], "error": ""}


def test_the_workspace_is_sent_as_a_tenant_header():
    """Without it, `/workspaces/current` is the KEY's workspace, not the assistant's."""
    assert rt._headers("k", "ws-9")["X-Tenant-Id"] == "ws-9"
    assert "X-Tenant-Id" not in rt._headers("k", None)
