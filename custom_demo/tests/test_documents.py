"""Versioned documents: the store's guarantees and the status codes the SPA branches on.

The fake client below is a small commit log with the two behaviours the real Hub has and
the store depends on: a push is a DELTA against `parent_commit` rather than a whole tree,
and a commit covers the repo rather than one file. Get either wrong and the tests would
pass while the product silently lost documents.
"""

from __future__ import annotations

from datetime import UTC, datetime
from json import JSONDecodeError
from types import SimpleNamespace
from typing import Any, cast

import pytest
from deepagents.backends import ContextHubBackend
from langsmith.schemas import FileEntry
from langsmith.utils import LangSmithConflictError, LangSmithNotFoundError
from starlette.testclient import TestClient

import custom_demo.resources.docs as D
import custom_demo.runtime.backends as B
from custom_demo.resources.docs import (
    DocumentConflict,
    DocumentNotFound,
    DocumentStoreError,
    list_documents,
    list_versions,
    read_document,
    repo_path,
    write_document,
)
from custom_demo.webapp import app

REPO = "acme-docs"
BRIEF = "/workspace/artifacts/req-204/brief.md"
SPEC = "req-204/functional-spec.md"


class FakeHub:
    """An in-memory Hub: an ordered commit log of whole trees, built from deltas."""

    def __init__(self):
        """Start with no repos at all, which is what a named-but-unwritten repo is."""
        self.commits: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.fail_with: Exception | None = None
        self.pushes: list[dict] = []

    def scoped(self, _workspace):
        return self

    def _log(self, identifier: str):
        if self.fail_with is not None:
            raise self.fail_with

        if identifier not in self.commits:
            raise LangSmithNotFoundError(f"no repo {identifier}")

        return self.commits[identifier]

    def pull_agent(self, identifier, *, version=None):
        log = self._log(identifier)
        if version is None:
            commit_hash, tree = log[-1]
        else:
            found = next(((h, t) for h, t in log if h == version), None)
            if found is None:
                raise LangSmithNotFoundError(f"no commit {version}")

            commit_hash, tree = found

        files = {path: SimpleNamespace(content=content) for path, content in tree.items()}
        return SimpleNamespace(commit_hash=commit_hash, files=files)

    def push_agent(self, identifier, *, files, parent_commit=None, **_kwargs):
        if self.fail_with is not None:
            raise self.fail_with

        log = self.commits.setdefault(identifier, [])
        base = dict(log[-1][1]) if log else {}
        self.pushes.append({"parent": parent_commit, "paths": sorted(files)})
        for path, entry in files.items():
            if entry is None:
                base.pop(path, None)
            else:
                base[path] = entry.content

        commit_hash = f"c{len(log) + 1:04d}"
        log.append((commit_hash, base))
        return f"https://hub/{identifier}:{commit_hash}"

    def list_prompt_commits(self, identifier, *, limit=None, **_kwargs):
        log = self._log(identifier)
        at = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
        newest_first = list(reversed(log))[: limit or len(log)]
        return [
            SimpleNamespace(commit_hash=commit_hash, created_at=at)
            for commit_hash, _ in newest_first
        ]


@pytest.fixture
def hub(monkeypatch):
    fake = FakeHub()
    monkeypatch.setattr(D, "scoped_client", fake.scoped)
    return fake


# --- paths ---


@pytest.mark.parametrize(
    "given,expected",
    [
        ("/workspace/artifacts/req-204/brief.md", "req-204/brief.md"),
        ("req-204/brief.md", "req-204/brief.md"),
        ("/req-204/brief.md", "req-204/brief.md"),
    ],
)
def test_the_spa_path_and_the_stored_path_are_the_same_document(given, expected):
    assert repo_path(given) == expected


@pytest.mark.parametrize("bad", ["", "/workspace/artifacts/", "../other/brief.md", "a/../../b.md"])
def test_a_path_that_leaves_the_repo_is_refused(bad):
    with pytest.raises(DocumentNotFound):
        repo_path(bad)


# --- reading and writing ---


def test_a_save_records_the_content_the_role_and_the_reason_in_one_commit(hub):
    saved = write_document(
        REPO, BRIEF, "# Brief\n", message="Drafted the brief", author="Intake Agent"
    )
    assert saved.version == "c0001"
    # ONE commit carrying both paths: a message pushed separately could land without
    # the document it describes, or not land at all.
    assert hub.pushes == [{"parent": None, "paths": ["req-204/.history.jsonl", "req-204/brief.md"]}]

    document = read_document(REPO, BRIEF)
    assert document.content == "# Brief\n"
    assert document.message == "Drafted the brief"
    assert document.author == "Intake Agent"
    assert document.path == BRIEF


def test_an_unwritten_repo_reads_as_empty_but_a_missing_document_raises(hub):
    assert list_documents(REPO).entries == []
    with pytest.raises(DocumentNotFound):
        read_document(REPO, BRIEF)


def test_an_unreachable_hub_is_never_reported_as_an_empty_document(hub):
    hub.fail_with = RuntimeError("hub is down")
    with pytest.raises(DocumentStoreError) as caught:
        read_document(REPO, BRIEF)

    assert "hub is down" in str(caught.value)
    assert not isinstance(caught.value, DocumentNotFound)


def test_a_save_against_a_stale_revision_is_refused_rather_than_applied(hub):
    first = write_document(REPO, BRIEF, "one", message="first")
    write_document(REPO, BRIEF, "two", message="second", base_version=first.version)
    with pytest.raises(DocumentConflict):
        write_document(REPO, BRIEF, "clobber", base_version=first.version)

    # The refused save left the winning revision in place.
    assert read_document(REPO, BRIEF).content == "two"


def test_a_save_with_no_base_revision_is_allowed(hub):
    """The agent's own writes carry no base: they are authoring, not resolving an edit."""
    write_document(REPO, BRIEF, "one")
    write_document(REPO, BRIEF, "two")
    assert read_document(REPO, BRIEF).content == "two"


def test_an_old_revision_still_reads_back_as_it_was(hub):
    first = write_document(REPO, BRIEF, "original")
    write_document(REPO, BRIEF, "replaced", base_version=first.version)
    assert read_document(REPO, BRIEF, version=first.version).content == "original"


def test_a_sibling_document_keeps_its_own_content_through_another_save(hub):
    write_document(REPO, SPEC, "# Spec\n")
    write_document(REPO, BRIEF, "# Brief\n")
    assert read_document(REPO, SPEC).content == "# Spec\n"


# --- revisions ---


def test_a_revision_records_the_person_and_the_hat_separately(hub):
    """Two facts, not one string: who saved it, and which role they were acting as.

    Joining them at write time ("Jo (UX Designer)") makes both unrecoverable, and a
    review chain asks different questions of each.
    """
    write_document(
        REPO, BRIEF, "# Brief\n", message="Tightened the wording", author="Jo", role="UX Designer"
    )
    document = read_document(REPO, BRIEF)
    assert document.author == "Jo"
    assert document.role == "UX Designer"
    version = list_versions(REPO, BRIEF)[0]
    assert (version.author, version.role) == ("Jo", "UX Designer")


def test_a_save_with_no_person_named_still_records_the_role(hub):
    """The name comes from the browser and may simply not be set."""
    write_document(REPO, BRIEF, "x", role="Product Owner")
    version = list_versions(REPO, BRIEF)[0]
    assert version.author == ""
    assert version.role == "Product Owner"


def test_only_the_commits_that_changed_this_document_are_its_revisions(hub):
    write_document(REPO, BRIEF, "one", message="drafted", author="Intake Agent")
    write_document(REPO, SPEC, "# Spec\n", message="spec drafted", author="PM Agent")
    write_document(REPO, BRIEF, "two", message="enriched", author="Kevin")

    versions = list_versions(REPO, BRIEF)
    assert [(v.message, v.author) for v in versions] == [
        ("enriched", "Kevin"),
        ("drafted", "Intake Agent"),
    ]
    assert versions[0].created_at


def test_a_document_in_an_unwritten_repo_has_no_revisions(hub):
    assert list_versions(REPO, BRIEF) == []


def test_the_listing_groups_by_folder_and_hides_the_save_log(hub):
    write_document(REPO, BRIEF, "one")
    write_document(REPO, SPEC, "two")
    listing = list_documents(REPO)
    assert [(e.folder, e.name) for e in listing.entries] == [
        ("req-204", "brief.md"),
        ("req-204", "functional-spec.md"),
    ]
    assert listing.version == "c0002"


# --- the agent's own writes, against a store a person is also writing to ---


class RacingHub:
    """A Hub that rejects the first push with a conflict, as a moved head does."""

    def __init__(self, conflicts: int):
        """Reject the first `conflicts` pushes, then accept."""
        self.conflicts = conflicts
        self.tree = {"req-204/brief.md": "written by someone else"}
        self.head = "c0009"
        self.pushes = 0

    def pull_agent(self, _identifier, *, version=None):
        # Real FileEntry objects: the vendor backend narrows on the type and reads a
        # linked repo handle off anything else.
        files = {path: FileEntry(type="file", content=body) for path, body in self.tree.items()}
        return SimpleNamespace(commit_hash=self.head, files=files)

    def push_agent(self, _identifier, *, files, parent_commit=None, **_kwargs):
        self.pushes += 1
        if self.conflicts > 0:
            self.conflicts -= 1
            raise LangSmithConflictError("head moved")

        for path, entry in files.items():
            self.tree[path] = entry.content

        self.head = f"c{9 + self.pushes:04d}"
        return f"https://hub/x:{self.head}"


def test_the_agent_write_rebases_when_a_person_committed_first():
    """A conflict here is usually two people editing two different documents.

    The person's revision has to survive and the agent's turn has to finish, so the
    write reloads and replays rather than failing the turn or overwriting the tree.
    """
    hub = RacingHub(conflicts=1)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    backend.write("/req-204/functional-spec.md", "drafted by the agent")
    assert hub.pushes == 2  # the refused push, then the replay
    assert hub.tree["req-204/functional-spec.md"] == "drafted by the agent"
    assert hub.tree["req-204/brief.md"] == "written by someone else"


def test_a_second_conflict_stops_and_reports_rather_than_retrying_forever():
    """One replay, then the failure reaches the caller.

    `ContextHubBackend.write` turns a Hub error into a `WriteResult` carrying `error`
    rather than raising, so what the agent sees is a failed tool result. That is the
    right place for it to stop: a loop replaying over a second writer's commits would
    eventually bury one of them.
    """
    hub = RacingHub(conflicts=2)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    result = backend.write("/req-204/functional-spec.md", "drafted by the agent")
    assert hub.pushes == 2
    assert result.error
    assert "req-204/functional-spec.md" not in hub.tree


class GarbledHub(RacingHub):
    """A Hub that answers the first `garbled` pushes with a body the SDK cannot parse.

    Reported from production: `push_agent` reads `response.json()["commit"]` and the
    response carried HTML, so a `JSONDecodeError` came out of the push. It is not a
    `LangSmithError`, which is the only family the vendor backend converts into a
    result, so it escaped the tool, the graph and the turn. `commits` says whether the
    delta reached the tree before the response was mangled, which is the difference
    between work to keep and work to replay.
    """

    def __init__(self, garbled: int, *, commits: bool = False):
        """Mangle the response of the first `garbled` pushes, committing them or not."""
        super().__init__(conflicts=0)
        self.garbled = garbled
        self.commits = commits

    def push_agent(self, _identifier, *, files, parent_commit=None, **_kwargs):
        self.pushes += 1
        mangled = self.garbled > 0
        self.garbled -= 1
        if not mangled or self.commits:
            # A `None` entry is the deletion marker the real Hub drops from the tree.
            for path, entry in files.items():
                if entry is None:
                    self.tree.pop(path, None)
                else:
                    self.tree[path] = entry.content

            self.head = f"c{9 + self.pushes:04d}"

        if mangled:
            raise JSONDecodeError("Expecting value", "<html>502</html>", 0)

        return f"https://hub/x:{self.head}"


def test_a_push_the_hub_never_confirmed_is_replayed_rather_than_failing_the_turn():
    """The delete that killed a turn: an unparseable push response is not a tool result.

    It has to become one. The turn carries the rest of the agent's work, and a
    traceback out of the delete tool discards all of it.
    """
    hub = GarbledHub(garbled=1)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    result = backend.delete("/req-204/brief.md")
    assert result.error is None
    assert hub.pushes == 2  # the mangled response, then the replay
    assert "req-204/brief.md" not in hub.tree


def test_work_the_hub_did_record_is_kept_rather_than_pushed_twice():
    """An unparseable response says nothing about what the repo now holds.

    So re-read it. The delta was committed here, and reporting that delete as failed
    would leave the agent telling the user it did not do work it actually did.
    """
    hub = GarbledHub(garbled=1, commits=True)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    result = backend.delete("/req-204/brief.md")
    assert result.error is None
    assert hub.pushes == 1  # the tree already agreed, so nothing was replayed
    assert "req-204/brief.md" not in hub.tree


def test_a_write_the_hub_keeps_refusing_reaches_the_model_as_a_failed_tool_result():
    """One replay, then the failure is reported by naming the path, not by raising."""
    hub = GarbledHub(garbled=2)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    result = backend.write("/req-204/functional-spec.md", "drafted by the agent")
    assert hub.pushes == 2
    assert result.error and "req-204/functional-spec.md" in result.error
    assert "req-204/functional-spec.md" not in hub.tree


def test_an_edit_the_hub_never_confirmed_is_reported_and_not_replayed():
    """A replacement is not idempotent, so replaying one the repo may hold applies it twice."""
    hub = GarbledHub(garbled=1, commits=True)
    backend = B.DocumentsBackend("acme-docs", client=cast("Any", hub))
    result = backend.edit("/req-204/brief.md", "someone else", "the agent")
    assert hub.pushes == 1
    assert result.error and "req-204/brief.md" in result.error


def test_the_private_commit_hook_this_depends_on_still_exists():
    """Pins the dependency on a deepagents-internal method.

    `DocumentsBackend` overrides `ContextHubBackend._commit`. A release that renames it
    would leave the override dead and silently restore the failed turns, so the rename
    has to fail here instead.
    """
    assert callable(ContextHubBackend._commit)
    assert B.DocumentsBackend._commit is not ContextHubBackend._commit


def test_documents_backends_are_never_shared_between_runs(monkeypatch):
    """Two resolves must not share a backend, because it caches the tree it loaded.

    A shared one keeps serving the snapshot it first read, so after a person saves in the
    browser the agent reads superseded text and reasons over documents that have moved.
    """
    monkeypatch.setattr(B, "scoped_client", lambda workspace: None)
    built = []

    def record(repo, client=None):
        built.append(repo)
        return SimpleNamespace(_repo=repo)

    monkeypatch.setattr(B, "DocumentsBackend", record)
    first = B._docs_route("acme-docs", None)[B.ARTIFACTS_MOUNT]
    second = B._docs_route("acme-docs", None)[B.ARTIFACTS_MOUNT]
    assert built == ["acme-docs", "acme-docs"]
    assert first is not second


# --- HTTP ---


def _client():
    return TestClient(app)


def test_a_route_without_a_documents_repo_says_so_rather_than_failing(hub):
    response = _client().get("/docs-files")
    assert response.status_code == 400
    assert response.json()["reason"] == "no_docs_repo"


def test_reading_a_missing_document_is_a_404_the_editor_can_branch_on(hub):
    response = _client().get("/docs-file", params={"docs_repo": REPO, "path": BRIEF})
    assert response.status_code == 404
    assert response.json()["reason"] == "not_found"


def test_an_unreachable_store_is_a_502_not_a_404(hub):
    hub.fail_with = RuntimeError("hub is down")
    response = _client().get("/docs-file", params={"docs_repo": REPO, "path": BRIEF})
    assert response.status_code == 502
    assert response.json()["reason"] == "store_unavailable"


def test_a_lost_race_is_a_409_carrying_the_current_revision(hub):
    first = write_document(REPO, BRIEF, "one")
    write_document(REPO, BRIEF, "two", base_version=first.version)
    response = _client().post(
        "/docs-file",
        json={
            "docs_repo": REPO,
            "path": BRIEF,
            "content": "clobber",
            "base_version": first.version,
        },
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "conflict"
    assert "c0002" in response.json()["error"]


def test_a_save_over_http_becomes_a_revision_with_its_message(hub):
    response = _client().post(
        "/docs-file",
        json={
            "docs_repo": REPO,
            "path": BRIEF,
            "content": "# Brief\n",
            "message": "PO approved",
            "author": "Kevin",
            "role": "Product Owner",
        },
    )
    assert response.status_code == 200
    assert response.json()["version"] == "c0001"
    versions = _client().get("/docs-versions", params={"docs_repo": REPO, "path": BRIEF}).json()
    assert versions["versions"][0]["message"] == "PO approved"
    assert versions["versions"][0]["author"] == "Kevin"
    assert versions["versions"][0]["role"] == "Product Owner"


def test_a_save_without_text_is_refused_before_it_reaches_the_store(hub):
    response = _client().post("/docs-file", json={"docs_repo": REPO, "path": BRIEF})
    assert response.status_code == 400
    assert response.json()["reason"] == "no_content"
    assert hub.pushes == []
