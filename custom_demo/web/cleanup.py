"""`POST /cleanup`: cascade-delete everything an assistant's setup run created.

The handles travel as the `ls_artifacts` dict that `provisioning/setup.py` writes,
the SPA stores on the assistant and POSTs back verbatim. `test_cleanup_contract.py`
pins the manifest against the ordered deletion behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from starlette.responses import JSONResponse

from custom_demo.config import scoped_client
from custom_demo.core.demo import LsArtifacts
from custom_demo.provisioning.evals import _rules_api, delete_judge_evaluator


def _403_detail(exc: Exception) -> str:
    """The server's own explanation of a 403, or as much of the body as fits."""
    response = getattr(exc, "response", None)
    if response is None:
        return ""

    try:
        return str(response.json().get("detail") or "")
    except Exception:  # noqa: BLE001 - a non-JSON body is no reason to lose the 403
        return (response.text or "")[:200]


def _delete_error(exc: Exception) -> str:
    """A cleanup failure in words the reader can act on.

    A raw httpx error is three lines of URL and a link to MDN's page on 403, which tells a
    presenter nothing. A 403 here is not a bug and not transient: it is a role that lacks a
    named permission, and naming it is the difference between "try again" and "ask an admin
    for rules:delete". The server already says which one in the body.
    """
    text = str(exc)
    if "403" not in text and "Forbidden" not in text:
        return f"{type(exc).__name__}: {exc}"

    detail = _403_detail(exc)
    for token in ("permission ", "missing permission "):
        if token in detail:
            return f"not permitted: needs the {detail.split(token)[-1].strip()} permission"

    return "not permitted (403) - the API key's role is missing a delete permission"


async def cleanup(request):
    """Best-effort cascade delete of the LangSmith artifacts an assistant created.

    Body: {workspace, project?, agent_repo?, skills?[], eval_dataset?}.
    Deletes the trace project, the Context Hub agent repo, any
    linked skill repos, and the assistant's eval dataset. Each deletion is
    independent; failures (e.g. missing perms or an already-deleted artifact) are
    collected rather than aborting the rest.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - an unreadable body is a client error; answer 400, not 500
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    return JSONResponse(await asyncio.to_thread(_delete_artifacts, body))


def _delete_artifacts(body: dict) -> dict:
    """Delete manifest artifacts sequentially, collecting independent failures."""
    artifacts = LsArtifacts.from_mapping(body)
    workspace = artifacts.workspace
    client = scoped_client(workspace)
    deleted: list[str] = []
    failed: list[dict] = []

    def _try(kind: str, name: str | None, fn: Callable[[str], object]) -> None:
        """Pass a nonempty handle to its deletion and record the outcome."""
        if not name:
            return

        try:
            fn(name)
            deleted.append(f"{kind}:{name}")
        except Exception as exc:  # noqa: BLE001 - per-artifact: collected into `failed` so the rest of the cascade runs
            failed.append({"artifact": f"{kind}:{name}", "error": _delete_error(exc)})

    _try("project", artifacts.project, lambda name: client.delete_project(project_name=name))
    _try("agent", artifacts.agent_repo, lambda name: client.delete_agent(name))
    _try("skills bundle", artifacts.skills_repo, lambda name: client.delete_agent(name))
    for skill in artifacts.skills or []:
        _try("skill", skill, lambda name: client.delete_skill(name))

    _try(
        "eval dataset",
        artifacts.eval_dataset,
        lambda name: client.delete_dataset(dataset_name=name),
    )
    _try("eval rule", artifacts.eval_rule_id, lambda name: _delete_eval_rule(workspace, name))
    _try(
        "eval evaluator",
        artifacts.eval_evaluator_id,
        lambda name: delete_judge_evaluator(workspace, name),
    )
    _try(
        "annotation queue",
        artifacts.annotation_queue,
        lambda name: _delete_annotation_queue(workspace, name),
    )
    _try("eval judge prompt", artifacts.eval_judge_prompt, lambda name: client.delete_prompt(name))

    return {"deleted": deleted, "failed": failed}


def _delete_eval_rule(workspace: str | None, rule_id: str) -> None:
    """DELETE the run rule `rule_id`. Raises on failure, so `_try` records it."""
    url, headers = _rules_api(workspace)
    res = httpx.delete(f"{url}/{rule_id}", headers=headers, timeout=30)
    res.raise_for_status()


def _delete_annotation_queue(workspace: str | None, name: str) -> None:
    """Delete the annotation queue called `name`. Raises so `_try` records it.

    A queue that was never created (the backfill failed, or the assistant predates the
    feature) is not a failure: there is nothing to delete and the report stays quiet.
    """
    client = scoped_client(workspace)
    queue = next((q for q in client.list_annotation_queues(name=name, limit=1) or []), None)
    if queue is not None:
        client.delete_annotation_queue(queue.id)
