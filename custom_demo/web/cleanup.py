"""`POST /cleanup`: cascade-delete everything an assistant's setup run created.

The handles travel as the `ls_artifacts` dict that `provisioning/setup.py` writes,
the SPA stores on the assistant and POSTs back verbatim. `test_cleanup_contract.py`
pins the three sides against each other, and reads this file to do it — the guard
keys below are half of that contract.
"""

from __future__ import annotations

import httpx
from starlette.responses import JSONResponse

from custom_demo.config import scoped_client
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

    client = scoped_client(body.get("workspace"))
    deleted: list[str] = []
    failed: list[dict] = []

    def _try(kind: str, name: str, fn):
        if not name:
            return

        try:
            fn()
            deleted.append(f"{kind}:{name}")
        except Exception as exc:  # noqa: BLE001 - per-artifact: collected into `failed` so the rest of the cascade runs
            failed.append({"artifact": f"{kind}:{name}", "error": _delete_error(exc)})

    _try(
        "project", body.get("project"), lambda: client.delete_project(project_name=body["project"])
    )
    _try("agent", body.get("agent_repo"), lambda: client.delete_agent(body["agent_repo"]))
    # The skills bundle is an agent-type repo (push_agent) → delete_agent, not delete_skill.
    _try(
        "skills bundle",
        body.get("skills_repo"),
        lambda: client.delete_agent(body["skills_repo"]),
    )
    for skill in body.get("skills") or []:  # legacy per-skill repos
        _try("skill", skill, lambda s=skill: client.delete_skill(s))

    # The per-assistant eval dataset (provisioning/setup.py writes it into
    # `ls_artifacts.eval_dataset`). Absent for assistants created before that
    # feature, and `_try` no-ops on a falsy name — so this stays a silent skip.
    _try(
        "eval dataset",
        body.get("eval_dataset"),
        lambda: client.delete_dataset(dataset_name=body["eval_dataset"]),
    )
    # The run rule that attached the judge to that dataset. Deleted explicitly because
    # dropping the dataset is not documented to cascade to it, and a leftover rule shows
    # up as a stray evaluator in the customer's workspace.
    _try(
        "eval rule",
        body.get("eval_rule_id"),
        lambda: _delete_eval_rule(body.get("workspace"), body["eval_rule_id"]),
    )
    # The evaluator the rule pointed at — a separate workspace-level object, and the row
    # on the Evaluators page. Deleting the rule does not remove it, so without this every
    # torn-down demo leaves one behind in the customer's workspace. After the rule, since
    # an evaluator with a live attachment may refuse to go.
    _try(
        "eval evaluator",
        body.get("eval_evaluator_id"),
        lambda: _delete_judge_evaluator(body.get("workspace"), body["eval_evaluator_id"]),
    )
    # The human-review queue over the trace project. Recorded by name (it is created on
    # the backfill thread, after the assistant's metadata is written), so this resolves
    # the name to an id first. Deleting the project does not take the queue with it.
    _try(
        "annotation queue",
        body.get("annotation_queue"),
        lambda: _delete_annotation_queue(body.get("workspace"), body["annotation_queue"]),
    )
    # The judge prompt the evaluator referenced. Deleted after it, since a prompt with a
    # live reference may refuse to go.
    _try(
        "eval judge prompt",
        body.get("eval_judge_prompt"),
        lambda: client.delete_prompt(body["eval_judge_prompt"]),
    )

    return JSONResponse({"deleted": deleted, "failed": failed})


def _delete_judge_evaluator(workspace: str | None, evaluator_id: str) -> None:
    """DELETE the workspace evaluator `evaluator_id`. Raises so `_try` records it.

    Kept as a wrapper despite forwarding both arguments unchanged. An audit
    flagged it as a pure pass-through, but two tests patch this name, so removing
    it would take the seam they use with it.
    """
    delete_judge_evaluator(workspace, evaluator_id)


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
