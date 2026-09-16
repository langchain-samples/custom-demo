"""Versioned documents in a Context Hub repo, as read and write operations.

The agent writes documents through the filesystem mount that `runtime/backends.py`
points at this repo, so every write is already a Hub commit. This module is the OTHER
side of the same store: the browser reading a document, listing its revisions, and
saving an edit a person made by hand.

Two facts about Hub shape the code here.

`push_agent`'s `description` updates the REPO's description, not the commit's: the
commit body carries only `files` and `parent_commit`. So a save that wants to say what
it changed has to put that sentence somewhere durable itself, and the one place that
travels with the commit is the commit's own tree. Each save therefore writes two paths
in ONE commit: the document, and a line appended to `.history.jsonl` beside it. The
commit containing a line IS the commit that line describes, so no second push and no
guessing which message belongs to which revision.

A commit covers the whole tree rather than one file, so "this document's revisions"
means reading trees and keeping the commits where this path's content actually changed.
That is one network call per revision, which is why `list_versions` is capped and
fetches concurrently; it runs when someone opens a history panel, not on every read.
"""

from __future__ import annotations

import json
import posixpath
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from langsmith.schemas import FileEntry
from langsmith.utils import LangSmithNotFoundError
from pydantic import BaseModel

from custom_demo.config import scoped_client

# The filesystem prefix the SPA speaks in. Documents are stored WITHOUT it: the runtime
# mounts this repo at that prefix and CompositeBackend strips it on the way through, so
# `/workspace/artifacts/req-204/brief.md` is `req-204/brief.md` here.
ARTIFACTS_PREFIX = "/workspace/artifacts/"

# Per-folder save log. Named with a leading dot so `list_documents` can hide it: it is
# bookkeeping about the folder, not a document someone opens.
HISTORY_FILE = ".history.jsonl"

# Revisions examined by `list_versions`. Each one costs a tree fetch, and a document
# nobody has opened thirty times does not need a thirty-deep history panel.
MAX_VERSIONS = 25

# Concurrent tree fetches. Small on purpose: this shares the process-wide default
# executor with the sandbox routes and langgraph's own serde.
_FETCH_WORKERS = 6


class DocumentStoreError(RuntimeError):
    """A documents repo could not be read or written, naming the repo and the cause."""


class DocumentNotFound(DocumentStoreError):
    """No such document, or no such revision of it. Distinct from an unreachable store."""


class DocumentConflict(DocumentStoreError):
    """The document changed since the editor loaded it, so the save was not applied."""


class DocumentVersion(BaseModel):
    """One revision of one document, as a history panel shows it."""

    version: str
    created_at: str
    message: str = ""
    author: str = ""


class DocumentEntry(BaseModel):
    """One document in the repo, grouped by the folder that bundles its request."""

    path: str
    folder: str
    name: str
    bytes: int


class DocumentListing(BaseModel):
    """Every document in the repo at one revision."""

    repo: str
    version: str = ""
    entries: list[DocumentEntry] = []


class Document(BaseModel):
    """One document's content at one revision."""

    path: str
    content: str
    version: str = ""
    message: str = ""
    author: str = ""


class DocumentSaved(BaseModel):
    """The revision a save created."""

    path: str
    version: str
    message: str = ""


def repo_path(path: str) -> str:
    """A repo-relative document path, accepting the SPA's absolute filesystem path.

    Both spellings reach here: the browser holds `/workspace/artifacts/req/brief.md`
    because that is what the agent's write announced, while the repo stores
    `req/brief.md`. Normalizing in one place keeps every caller free to pass whichever
    one it already has.
    """
    clean = (path or "").strip()
    if clean.startswith(ARTIFACTS_PREFIX):
        clean = clean[len(ARTIFACTS_PREFIX) :]

    clean = clean.lstrip("/")
    # `..` would address another assistant's documents in the same workspace.
    if not clean or any(part in {"..", "."} for part in clean.split("/")):
        raise DocumentNotFound(f"{path!r} is not a document path inside this repo.")

    return clean


def history_path(path: str) -> str:
    """Where the save log for `path`'s folder lives."""
    folder = posixpath.dirname(repo_path(path))
    return posixpath.join(folder, HISTORY_FILE) if folder else HISTORY_FILE


def _pull(repo: str, ws: str | None, version: str | None = None) -> dict[str, str]:
    """This repo's file tree at `version`, or at HEAD, as path to content.

    A repo that does not exist yet is an EMPTY tree rather than a failure: the
    assistant named the repo and nothing has been written to it, which is a true and
    useful answer. Every other failure is raised, because a caller that silently reads
    an empty document would then happily overwrite a document that does exist.
    """
    try:
        context = scoped_client(ws).pull_agent(repo, version=version)
    except LangSmithNotFoundError:
        if version:
            raise DocumentNotFound(
                f"Revision {version!r} of documents repo {repo!r} could not be read."
            ) from None

        return {}
    except Exception as exc:
        raise DocumentStoreError(
            f"Documents repo {repo!r} could not be read: {type(exc).__name__}: {exc}"
        ) from exc

    tree: dict[str, str] = {}
    for path, entry in (context.files or {}).items():
        content = getattr(entry, "content", None)
        if isinstance(content, str):
            tree[path] = content

    return tree


def _head(repo: str, ws: str | None) -> str:
    """The repo's current commit hash, or "" when nothing has been committed."""
    try:
        context = scoped_client(ws).pull_agent(repo)
    except LangSmithNotFoundError:
        return ""
    except Exception as exc:
        raise DocumentStoreError(
            f"Documents repo {repo!r} could not be read: {type(exc).__name__}: {exc}"
        ) from exc

    return str(context.commit_hash or "")


def _history(tree: dict[str, str], path: str) -> list[dict[str, Any]]:
    """Save-log entries for `path` recorded in `tree`, oldest first.

    A malformed line is skipped rather than raised on: the log is a label for a human,
    and one bad line must not make a document's history unreadable.
    """
    raw = tree.get(history_path(path), "")
    target = repo_path(path)
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            entry = json.loads(line)
        except ValueError:
            continue

        if isinstance(entry, dict) and entry.get("path") == target:
            out.append(entry)

    return out


def list_documents(repo: str, ws: str | None = None) -> DocumentListing:
    """Every document in the repo, with the folder that bundles it."""
    tree = _pull(repo, ws)
    entries = [
        DocumentEntry(
            path=ARTIFACTS_PREFIX + path,
            folder=posixpath.dirname(path),
            name=posixpath.basename(path),
            bytes=len(content.encode("utf-8")),
        )
        for path, content in sorted(tree.items())
        if not posixpath.basename(path).startswith(".")
    ]
    return DocumentListing(repo=repo, version=_head(repo, ws), entries=entries)


def read_document(
    repo: str, path: str, ws: str | None = None, version: str | None = None
) -> Document:
    """One document's content, at `version` or at HEAD.

    A path with no content in that revision raises: an editor that opened an empty
    buffer for a document that does exist would save that emptiness over it.
    """
    key = repo_path(path)
    tree = _pull(repo, ws, version)
    if key not in tree:
        where = f"revision {version}" if version else "this repo"
        raise DocumentNotFound(f"{key!r} is not in {where} of documents repo {repo!r}.")

    entries = _history(tree, key)
    last = entries[-1] if entries else {}
    return Document(
        path=ARTIFACTS_PREFIX + key,
        content=tree[key],
        version=version or _head(repo, ws),
        message=str(last.get("message") or ""),
        author=str(last.get("author") or ""),
    )


def write_document(
    repo: str,
    path: str,
    content: str,
    ws: str | None = None,
    message: str = "",
    author: str = "",
    base_version: str | None = None,
) -> DocumentSaved:
    """Save `content` as the next revision, recording who changed it and why.

    `base_version` is the revision the editor loaded. When it is no longer the repo's
    head, someone else has committed in the meantime and this raises `DocumentConflict`
    rather than pushing: the parent chain would accept the write, and the other person's
    revision would still be gone from the document the next reader opens.
    """
    key = repo_path(path)
    head = _head(repo, ws)
    if base_version and head and base_version != head:
        raise DocumentConflict(
            f"{key!r} changed since you opened it (you had {base_version[:12]}, "
            f"the current revision is {head[:12]}). Reload to see the other edit."
        )

    tree = _pull(repo, ws)
    entry = {
        "path": key,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "message": message.strip(),
        "author": author.strip(),
    }
    log = tree.get(history_path(key), "")
    if log and not log.endswith("\n"):
        log += "\n"

    files: dict[str, Any] = {
        key: FileEntry(type="file", content=content),
        history_path(key): FileEntry(type="file", content=log + json.dumps(entry) + "\n"),
    }
    try:
        scoped_client(ws).push_agent(repo, files=files, parent_commit=head or None)
    except Exception as exc:
        raise DocumentStoreError(
            f"Saving {key!r} to documents repo {repo!r} failed: {type(exc).__name__}: {exc}"
        ) from exc

    return DocumentSaved(path=ARTIFACTS_PREFIX + key, version=_head(repo, ws), message=message)


def list_versions(
    repo: str, path: str, ws: str | None = None, limit: int = MAX_VERSIONS
) -> list[DocumentVersion]:
    """Revisions in which this document's content changed, newest first.

    Hub commits cover the whole tree, so a commit that only touched a sibling document
    is not a revision of this one and is dropped. The oldest examined commit is kept
    whenever the document exists there, since there is no older tree in hand to prove
    the content arrived earlier.
    """
    key = repo_path(path)
    try:
        commits = list(scoped_client(ws).list_prompt_commits(repo, limit=max(1, limit)))
    except LangSmithNotFoundError:
        return []
    except Exception as exc:
        raise DocumentStoreError(
            f"Revisions of documents repo {repo!r} could not be listed: {type(exc).__name__}: {exc}"
        ) from exc

    hashes = [str(c.commit_hash) for c in commits if c.commit_hash]
    if not hashes:
        return []

    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        trees = dict(zip(hashes, pool.map(lambda h: _pull(repo, ws, h), hashes), strict=True))

    out: list[DocumentVersion] = []
    for index, commit in enumerate(commits):
        version = str(commit.commit_hash or "")
        tree = trees.get(version) or {}
        if key not in tree:
            continue

        older = hashes[index + 1] if index + 1 < len(hashes) else None
        if older is not None and (trees.get(older) or {}).get(key) == tree[key]:
            continue

        entries = _history(tree, key)
        last = entries[-1] if entries else {}
        created = commit.created_at
        out.append(
            DocumentVersion(
                version=version,
                created_at=created.isoformat() if created else "",
                message=str(last.get("message") or ""),
                author=str(last.get("author") or ""),
            )
        )

    return out
