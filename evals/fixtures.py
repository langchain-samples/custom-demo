"""Shared eval fixtures: LangSmith client, dataset upserts, and the eval agent.

Reuses the real setup helpers so the Context Hub eval agent the behavior evals
run against is built exactly like a demo one.
"""

from __future__ import annotations

import os

from langsmith import Client

from dashboard_agent.agent import Context
from dashboard_agent.assistant_setup import (
    _SKILLS_CLAUSE,
    DASHBOARD_SKILL,
    push_agent_prompt,
    push_workflow_skills,
)
from dashboard_agent.config import load_env
from dashboard_agent.prompt import build_system_prompt

# The planted gap the hallucination demo fabricates over (synthetic data returns
# nothing for this topic). Kept in one place so datasets + Context agree.
GAP = "customer satisfaction scores"

# A token only the marker skill knows — proves the agent read + applied the skill.
MARKER = "EVAL-MARK-7"

_AGENT_REPO = "evals-agent"
_SLUG = "evals"
_CUSTOMER = "Eval Co"
_INDUSTRY = "Retail"


def eval_workspace() -> str:
    """Workspace (tenant) id the eval datasets + fixtures live in."""
    load_env()
    return os.getenv("EVAL_WORKSPACE") or os.getenv("CTXHUB_TEST_WORKSPACE") or ""


def _ls_key() -> str:
    load_env()
    return os.getenv("LS_CROSS_WORKSPACE_KEY") or os.getenv("LANGSMITH_API_KEY") or ""


def eval_client() -> Client:
    """LangSmith client scoped to the eval workspace (datasets + experiments)."""
    api_url = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_key=_ls_key(), api_url=api_url, workspace_id=eval_workspace() or None)


def ensure_dataset(client: Client, name: str, examples: list[dict]) -> str:
    """Create `name` with `examples` if absent; return its id. Idempotent.

    `examples` are dicts with `inputs` and optional `outputs`/`metadata`. Existing
    non-empty datasets are left untouched so experiment history stays comparable.
    """
    if client.has_dataset(dataset_name=name):
        ds = client.read_dataset(dataset_name=name)
        if next(client.list_examples(dataset_id=ds.id, limit=1), None) is not None:
            return str(ds.id)
    else:
        ds = client.create_dataset(dataset_name=name)
    client.create_examples(dataset_id=ds.id, examples=examples)
    return str(ds.id)


def ensure_ctxhub_agent(workspace: str) -> tuple[str, str]:
    """Push the fixed eval Context Hub agent (idempotent). Returns (repo, marker).

    A hallucination-mode agent with two skills: the curated dashboard skill and a
    marker returns-eligibility skill whose instructions cite MARKER.
    """
    skills = [
        DASHBOARD_SKILL,
        {
            "name": "returns-eligibility",
            "description": "Use when a shopper asks about returns, refunds, or return eligibility.",
            "instructions": (
                f"When answering any returns or refund question, ALWAYS cite the internal "
                f"policy code {MARKER} in your reply."
            ),
            "example_question": "Can I return a cordless drill I bought 12 days ago?",
        },
    ]
    links = push_workflow_skills(workspace, _SLUG, _CUSTOMER, skills)
    agents_md = build_system_prompt(
        _CUSTOMER,
        _INDUSTRY,
        failure_mode="hallucination",
        use_case="in-store shopper support and operations",
        dashboard="skill",
    ) + (_SKILLS_CLAUSE if links else "")
    push_agent_prompt(workspace, _AGENT_REPO, agents_md, skill_links=links)
    return _AGENT_REPO, MARKER


def make_context(repo: str, **over) -> Context:
    """A runtime Context for the eval agent, overridable per example."""
    ctx = Context(
        agent_repo=repo,
        ls_workspace=eval_workspace(),
        dataset="synthetic",
        data_gap=GAP,
        customer=_CUSTOMER,
        industry=_INDUSTRY,
        enabled_tools=["datasearch", "push_widget"],
    )
    for key, value in over.items():
        setattr(ctx, key, value)
    return ctx
