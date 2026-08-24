"""Say why a demo dataset's `grounded` evaluator is not scoring — and fix it.

    uv run python scripts/judge_doctor.py --workspace <id> --dataset <name> [--repair]

The failure this exists for reads, on every row of every experiment:

    '400: Evaluator failed to batch invoke
     {"detail":"RunnableSequence must have at least 2 steps, got 0"}'

which means the judge's Prompt Hub commit holds a prompt and no model, so LangSmith has
no chain to invoke (see the block above `_RULES_PATH` in assistant_evals). `--repair`
re-commits that prompt with a model bound; the evaluator references it by `latest`, so it
starts scoring with no change to the evaluator or the rule.

Read-only without `--repair`. Needs a key that can see the workspace: LS_CROSS_WORKSPACE_KEY
if set, else LANGSMITH_API_KEY.

NOTE: repairing does NOT re-score runs that already errored — feedback is written once, per
run. Start a new experiment (the panel's Re-run) to see scores.
"""

from __future__ import annotations

import argparse
import sys

from dashboard_agent.assistant_evals import (
    EVAL_FEEDBACK_KEY,
    dataset_rules,
    ensure_judge_runnable,
    judge_has_model,
    judge_model_manifest,
    judge_prompt_name,
)
from dashboard_agent.assistant_setup import _ws_client
from dashboard_agent.config import load_env


def main() -> int:
    """Report each link in the chain from dataset to score, then optionally repair."""
    load_env()
    ap = argparse.ArgumentParser(description="Diagnose a demo dataset's LangSmith judge.")
    ap.add_argument("--workspace", required=True, help="LangSmith workspace (tenant) id")
    ap.add_argument(
        "--dataset", required=True, help="dataset name, e.g. acme-demo-evals-6683cd5a4a"
    )
    ap.add_argument("--repair", action="store_true", help="bind a model to the judge prompt")
    args = ap.parse_args()

    client = _ws_client(args.workspace)
    try:
        dataset_id = str(client.read_dataset(dataset_name=args.dataset).id)
    except Exception as exc:  # noqa: BLE001 - the wrong workspace is the common mistake
        print(f"dataset: NOT FOUND in this workspace ({type(exc).__name__}: {str(exc)[:120]})")
        return 1
    print(f"dataset:   {args.dataset} ({dataset_id})")

    # 1. Is anything attached? No rule means the panel grades in-process, which scores fine
    #    and is not this bug.
    rules = [r for r in dataset_rules(args.workspace, dataset_id) if r.get("display_name")]
    ours = [r for r in rules if r.get("display_name") == EVAL_FEEDBACK_KEY]
    print(f"rules:     {[r.get('display_name') for r in rules] or 'none (graded in-process)'}")
    if not ours:
        return 0
    print(f"evaluator: {ours[0].get('evaluator_id')}")

    # 2. The actual question: does the judge's prompt resolve to a chain?
    repo = judge_prompt_name(args.dataset)
    try:
        runnable = judge_has_model(client, repo)
    except Exception as exc:  # noqa: BLE001
        print(f"judge:     {repo} — CANNOT READ ({type(exc).__name__}: {str(exc)[:120]})")
        return 1
    print(
        f"judge:     {repo} — {'prompt | model (OK)' if runnable else 'PROMPT ONLY (0-step chain)'}"
    )

    # 3. What it could be bound to. Empty means this workspace has no model whose secret it
    #    holds, and the honest fix is to detach and let the in-process judge grade.
    model_id, model = judge_model_manifest(client)
    print(
        f"model:     {model_id or 'NONE this workspace can run'} {model.get('id', [''])[-1] if model else ''}"
    )

    if runnable:
        return 0
    if not args.repair:
        print("\nre-run with --repair to bind that model to the judge prompt")
        return 1
    if not model:
        print("\nnothing to bind: add a model secret (or an evaluator-capable playground model)")
        return 1
    print(
        f"\nrepairing… {'ok' if ensure_judge_runnable(args.workspace, args.dataset) else 'FAILED'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
