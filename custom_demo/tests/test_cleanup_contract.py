"""The setup manifest, SPA interface and cleanup behavior must agree."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import custom_demo.web.cleanup as WC
from custom_demo.core.demo import LsArtifacts
from custom_demo.provisioning import setup as S
from custom_demo.webapp import app

API_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api.ts"
WORKSPACE = "workspace-cleanup-contract"
CASES = [
    ("project", "project", "project-handle", "delete_project"),
    ("agent_repo", "agent", "agent-handle", "delete_agent"),
    ("skills_repo", "skills bundle", "bundle-handle", "delete_agent"),
    ("skills", "skill", "legacy-skill-handle", "delete_skill"),
    ("eval_dataset", "eval dataset", "dataset-handle", "delete_dataset"),
    ("eval_rule_id", "eval rule", "rule-handle", "delete_rule"),
    ("eval_evaluator_id", "eval evaluator", "evaluator-handle", "delete_evaluator"),
    ("annotation_queue", "annotation queue", "queue-handle", "delete_annotation_queue"),
    ("eval_judge_prompt", "eval judge prompt", "judge-handle", "delete_prompt"),
]
KEYS = {"workspace", *(key for key, _, _, _ in CASES)}
MANIFEST = {
    "workspace": WORKSPACE,
    **{key: [handle] if key == "skills" else handle for key, _, handle, _ in CASES},
}


class FakeClient:
    def __init__(self):
        """Record scoped deletion calls without contacting LangSmith."""
        self.calls = []
        self.scopes = []
        self.threads = []
        self.failures = set()
        self.queue_exists = True
        self.queue_lookups = []

    def record(self, method, handle):
        self.threads.append(threading.get_ident())
        self.calls.append((method, handle))
        if handle in self.failures:
            raise RuntimeError(f"cannot delete {handle}")

    def scoped(self, workspace):
        self.scopes.append(workspace)
        self.threads.append(threading.get_ident())
        return self

    def delete_project(self, *, project_name):
        self.record("delete_project", project_name)

    def delete_agent(self, name):
        self.record("delete_agent", name)

    def delete_skill(self, name):
        self.record("delete_skill", name)

    def delete_dataset(self, *, dataset_name):
        self.record("delete_dataset", dataset_name)

    def delete_prompt(self, name):
        self.record("delete_prompt", name)

    def delete_rule(self, workspace, rule_id):
        self.scopes.append(workspace)
        self.record("delete_rule", rule_id)

    def delete_evaluator(self, workspace, evaluator_id):
        self.scopes.append(workspace)
        self.record("delete_evaluator", evaluator_id)

    def list_annotation_queues(self, *, name, limit):
        self.threads.append(threading.get_ident())
        self.queue_lookups.append((name, limit))
        return [SimpleNamespace(id=f"id:{name}")] if self.queue_exists else []

    def delete_annotation_queue(self, queue_id):
        self.record("delete_annotation_queue", queue_id)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(WC, "scoped_client", fake.scoped)
    monkeypatch.setattr(WC, "_delete_eval_rule", fake.delete_rule)
    monkeypatch.setattr(WC, "delete_judge_evaluator", fake.delete_evaluator)
    return fake


def _post(body):
    response = TestClient(app).post("/cleanup", json=body)
    assert response.status_code == 200
    return response.json()


def _expected_call(method, handle):
    return (method, f"id:{handle}" if method == "delete_annotation_queue" else handle)


def test_artifact_record_serializes_exactly_the_contract():
    assert set(LsArtifacts().to_dict()) == KEYS
    assert LsArtifacts.from_mapping(MANIFEST).to_dict() == MANIFEST


def test_legacy_manifest_adapter_does_not_coerce_or_reject_values():
    artifacts = LsArtifacts.from_mapping({"project": [], "skills": None, "unrelated": "ignored"})
    assert artifacts.project == []
    assert artifacts.skills is None
    assert artifacts.workspace is None
    assert set(artifacts.to_dict()) == KEYS


def test_tagging_projects_only_supported_resources_from_the_manifest():
    artifacts = LsArtifacts.from_mapping(MANIFEST)
    assert artifacts.tagging_targets() == {
        "workspace": WORKSPACE,
        "project": "project-handle",
        "dataset": "dataset-handle",
        "prompts": ("judge-handle",),
        "agents": ("agent-handle", "bundle-handle"),
        "evaluator_id": "evaluator-handle",
    }
    assert LsArtifacts().tagging_targets()["agents"] == ()
    assert LsArtifacts().tagging_targets()["prompts"] == ()


def test_setup_writes_exactly_the_contract(monkeypatch):
    monkeypatch.setattr(
        S,
        "fetch_brand",
        lambda *_args: {"accent": "", "accent2": "", "accent_scraped": "", "logo": ""},
    )
    monkeypatch.setattr(
        S,
        "analyze_customer",
        lambda *_args: {
            "seed_files": [{"name": "inventory.csv", "kind": "csv", "columns": ["sku"]}],
        },
    )
    result = S.prepare_assistant(
        {"workspace": WORKSPACE, "customer": "Contract customer", "push_prompts": False}
    )
    assert set(result["metadata"]["ls_artifacts"]) == KEYS
    assert result["metadata"]["ls_artifacts"]["workspace"] == WORKSPACE


def test_the_spa_declares_exactly_the_contract():
    body = API_TS.read_text(encoding="utf-8")
    start = body.index("export interface LsArtifacts {")
    end = body.index("\n}", start)
    assert set(re.findall(r"^  ([a-z_]+)\??:", body[start:end], re.M)) == KEYS
    assert len(KEYS) == 10


@pytest.mark.parametrize("key,label,handle,method", CASES)
def test_each_manifest_handle_deletes_only_its_own_artifact(fake, key, label, handle, method):
    body = _post({"workspace": WORKSPACE, key: MANIFEST[key]})
    assert fake.calls == [_expected_call(method, handle)]
    assert body == {"deleted": [f"{label}:{handle}"], "failed": []}
    assert fake.scopes == [WORKSPACE] * (
        2 if key in {"annotation_queue", "eval_rule_id", "eval_evaluator_id"} else 1
    )
    if key == "annotation_queue":
        assert fake.queue_lookups == [(handle, 1)]


@pytest.mark.parametrize("key,label,handle,method", CASES)
@pytest.mark.parametrize("empty", [None, "", []])
def test_empty_handles_are_skipped(fake, key, label, handle, method, empty):
    body = _post({**MANIFEST, key: empty})
    remaining = [case for case in CASES if case[0] != key]
    assert fake.calls == [_expected_call(op, name) for _, _, name, op in remaining]
    assert body == {"deleted": [f"{kind}:{name}" for _, kind, name, _ in remaining], "failed": []}


def test_empty_manifest_deletes_nothing(fake):
    assert _post({}) == {"deleted": [], "failed": []}
    assert fake.calls == []
    assert fake.scopes == [None]


def test_cascade_order_labels_and_workspace(fake):
    body = _post(MANIFEST)
    assert fake.calls == [_expected_call(method, handle) for _, _, handle, method in CASES]
    assert body == {"deleted": [f"{label}:{handle}" for _, label, handle, _ in CASES], "failed": []}
    assert fake.scopes == [WORKSPACE] * 4
    assert fake.queue_lookups == [("queue-handle", 1)]


@pytest.mark.parametrize("key,label,handle,method", CASES)
def test_one_failure_does_not_abort_or_reorder_other_deletions(fake, key, label, handle, method):
    failed_handle = _expected_call(method, handle)[1]
    fake.failures.add(failed_handle)
    body = _post(MANIFEST)
    assert fake.calls == [_expected_call(op, name) for _, _, name, op in CASES]
    assert body == {
        "deleted": [f"{kind}:{name}" for other, kind, name, _ in CASES if other != key],
        "failed": [
            {
                "artifact": f"{label}:{handle}",
                "error": f"RuntimeError: cannot delete {failed_handle}",
            }
        ],
    }


def test_legacy_skills_iterate_in_order_and_skip_empty_handles(fake):
    assert _post({"workspace": WORKSPACE, "skills": ["one", "", None, "two"]}) == {
        "deleted": ["skill:one", "skill:two"],
        "failed": [],
    }
    assert fake.calls == [("delete_skill", "one"), ("delete_skill", "two")]


def test_a_missing_annotation_queue_is_a_successful_noop(fake):
    fake.queue_exists = False
    assert _post({"workspace": WORKSPACE, "annotation_queue": "absent"}) == {
        "deleted": ["annotation queue:absent"],
        "failed": [],
    }
    assert fake.calls == []
    assert fake.queue_lookups == [("absent", 1)]


def test_invalid_json_returns_400_without_constructing_a_client(fake):
    response = TestClient(app).post("/cleanup", content="{")
    assert response.status_code == 400
    assert response.json() == {"error": "invalid JSON body"}
    assert fake.scopes == []


def test_client_creation_errors_propagate(monkeypatch):
    def fail(_workspace):
        raise RuntimeError("workspace unavailable")

    monkeypatch.setattr(WC, "scoped_client", fail)
    with pytest.raises(RuntimeError, match="workspace unavailable"):
        _post({"workspace": WORKSPACE})


def test_the_whole_cascade_runs_on_one_worker_thread(fake):
    event_loop_thread = threading.get_ident()

    class Request:
        async def json(self):
            assert threading.get_ident() == event_loop_thread
            return MANIFEST

    response = asyncio.run(WC.cleanup(Request()))
    assert response.status_code == 200
    assert json.loads(response.body)["failed"] == []
    assert fake.threads
    assert len(set(fake.threads)) == 1
    assert event_loop_thread not in fake.threads
