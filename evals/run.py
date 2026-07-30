"""Run the Tier-3 LLM eval experiments against LangSmith.

    uv run python -m evals.run [--only setup|data|agent]

Each group upserts a dataset, runs a target (setup LLM / synthetic data source /
the deep agent) over its examples, scores them with evaluators, and records a
LangSmith experiment. Gated: prints how to set the env and exits if it's missing.
See evals/README.md.
"""

from __future__ import annotations

import argparse
import sys
import time

from dashboard_agent.assistant_setup import analyze_customer
from dashboard_agent.config import load_env
from dashboard_agent.datasource import get_datasource

from .evaluators import actions_relevant, agent_behavior, data_gap_respected
from .fixtures import (
    GAP,
    MARKER,
    ensure_ctxhub_agent,
    ensure_dataset,
    eval_client,
    eval_workspace,
    make_context,
)

# --- datasets (parameterized inputs; `outputs` = reference data for evaluators) ---

SETUP_EXAMPLES = [
    {
        "inputs": {
            "customer": "Safeway",
            "use_case": "in-store shopper support: stock, returns, weekly deals",
        }
    },
    {
        "inputs": {
            "customer": "Delta Air Lines",
            "use_case": "member concierge for flight status, rebooking, and SkyMiles",
        }
    },
]

DATA_EXAMPLES = [
    {"inputs": {"topic": f"{GAP} by quarter", "is_gap": True}},
    {"inputs": {"topic": "top selling produce categories last month", "is_gap": False}},
]

AGENT_EXAMPLES = [
    {
        "inputs": {
            "question": (
                "A customer asks whether they can return a cordless drill bought 12 days ago. "
                "Use your returns-eligibility skill and cite the policy code it specifies."
            ),
            "kind": "skill",
        },
        "outputs": {"marker": MARKER},
    },
    {
        "inputs": {
            "question": f"What is our {GAP} trend over the last 4 quarters? Give exact figures.",
            "kind": "gap",
        }
    },
    {
        "inputs": {
            "question": "Save a short note to notes/summary.md with today's top takeaway, then confirm.",
            "kind": "file",
        }
    },
]


# --- targets ---


def _setup_target(inputs: dict) -> dict:
    a = analyze_customer(inputs["customer"], use_case=inputs.get("use_case", ""))
    return {"actions": a.get("actions") or [], "skills": a.get("skills") or []}


def _data_target(inputs: dict) -> dict:
    ds = get_datasource(
        dataset_name="synthetic", data_gap=GAP, customer="Eval Co", industry="Retail"
    )
    return {"results": ds.search(inputs["topic"], k=3)}


def _agent_target_factory(repo: str):
    from dashboard_agent.agent import build_agent

    agent = build_agent()

    def target(inputs: dict) -> dict:
        question, kind = inputs["question"], inputs.get("kind", "")
        ctx = make_context(repo)
        for _ in range(6):  # tolerate transient Anthropic 529 overloads
            try:
                res = agent.invoke(
                    {"messages": [{"role": "user", "content": question}]},
                    config={
                        "configurable": {"thread_id": f"eval-{kind}-{abs(hash(question)) % 10000}"}
                    },
                    context=ctx,
                )
                break
            except Exception as exc:  # noqa: BLE001
                if "529" in str(exc) or "overload" in str(exc).lower():
                    time.sleep(8)
                    continue
                raise
        calls = [tc["name"] for m in res["messages"] for tc in (getattr(m, "tool_calls", []) or [])]
        ai = [
            m
            for m in res["messages"]
            if getattr(m, "type", None) == "ai" and isinstance(getattr(m, "content", None), str)
        ]
        return {"tool_calls": calls, "answer": ai[-1].content if ai else ""}

    return target


# --- run ---


def _run_group(client, name: str, examples: list[dict], target, evaluators: list) -> None:
    dataset = f"dashboard-agent-{name}"
    ensure_dataset(client, dataset, examples)
    print(f"\n=== experiment: {name} (dataset: {dataset}) ===")
    results = client.evaluate(
        target,
        data=dataset,
        evaluators=evaluators,
        experiment_prefix=f"da-{name}",
        max_concurrency=2,
    )
    url = getattr(getattr(results, "_manager", None), "experiment_name", None) or getattr(
        results, "experiment_name", ""
    )
    print(f"    done: {url}")


def main() -> int:
    """Parse `--only`, check env, and run the selected eval experiment groups."""
    load_env()
    ap = argparse.ArgumentParser(description="Run dashboard-agent Tier-3 LLM evals.")
    ap.add_argument("--only", choices=["setup", "data", "agent"], help="run just one group")
    args = ap.parse_args()

    import os

    if not (os.getenv("ANTHROPIC_API_KEY") and eval_workspace()):
        print(
            "Skipping: set ANTHROPIC_API_KEY + EVAL_WORKSPACE (a LangSmith workspace id), and a "
            "dataset-capable LANGSMITH_API_KEY (+ LANGSMITH_WORKSPACE_ID). See evals/README.md."
        )
        return 0

    client = eval_client()
    groups = args.only or "setup,data,agent"
    selected = groups.split(",") if isinstance(groups, str) else [groups]

    if "setup" in selected:
        _run_group(client, "setup", SETUP_EXAMPLES, _setup_target, [actions_relevant])
    if "data" in selected:
        _run_group(client, "data", DATA_EXAMPLES, _data_target, [data_gap_respected])
    if "agent" in selected:
        repo, _ = ensure_ctxhub_agent(eval_workspace())
        _run_group(client, "agent", AGENT_EXAMPLES, _agent_target_factory(repo), [agent_behavior])
    return 0


if __name__ == "__main__":
    sys.exit(main())
