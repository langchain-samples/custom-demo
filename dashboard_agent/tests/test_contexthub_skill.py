r"""LangSmith-logged e2e test: a Context Hub assistant loads and applies a skill.

Exercises the full Context Hub path end-to-end — the agent's prompt comes from an
agent repo's AGENTS.md, a linked skill repo mounts under /skills/ via
ContextHubBackend, and we assert the agent discovers /skills, reads the matching
SKILL.md, and follows it. Logged to LangSmith as an experiment via the pytest
integration (`@pytest.mark.langsmith`).

Slow (real LLM) and writes to a LangSmith workspace, so it is gated: set both
ANTHROPIC_API_KEY and CTXHUB_TEST_WORKSPACE to run. It uses fixed CI-fixture repo
names, re-pushed idempotently (no delete needed — the org key can push but not
delete).

The `@pytest.mark.langsmith` decorator logs the run as an experiment, which needs
a LangSmith key with dataset permissions. If that key can reach multiple
workspaces, also set LANGSMITH_WORKSPACE_ID so the dataset lands in the right one
(otherwise `/datasets` 403s in the key's default tenant). To run WITHOUT logging
to LangSmith, set LANGSMITH_TEST_TRACKING=false.

    ANTHROPIC_API_KEY=... CTXHUB_TEST_WORKSPACE=<workspace-id> \\
      LANGSMITH_API_KEY=<dataset-capable-key> LANGSMITH_WORKSPACE_ID=<workspace-id> \\
      uv run pytest dashboard_agent/tests/test_contexthub_skill.py --langsmith-output
"""

import os
import time
import uuid

import pytest
from langsmith import testing as t

from dashboard_agent.agent import Context, build_agent
from dashboard_agent.assistant_setup import (
    _SKILLS_CLAUSE,
    push_agent_prompt,
    push_workflow_skills,
)
from dashboard_agent.config import load_env
from dashboard_agent.prompt import build_system_prompt

load_env()

_WS = os.getenv("CTXHUB_TEST_WORKSPACE", "")

pytestmark = pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") and _WS),
    reason="ANTHROPIC_API_KEY and CTXHUB_TEST_WORKSPACE required",
)

_SLUG = "ci-ctxhub"
_AGENT_REPO = f"{_SLUG}-agent"
_FS_AGENT_REPO = f"{_SLUG}-fs-agent"
_MARKER = "RET-9-ALPHA"  # a token only the skill knows — proves the skill was applied


@pytest.fixture(scope="module", autouse=True)
def _cleanup_fixture_repos():
    """Delete the fixed fixture repos after the module runs so they don't accumulate.

    The repos are re-pushed idempotently at the start of each test, so deleting
    them here leaves the workspace clean between runs. Best-effort: skipped
    silently if the key lacks delete permission (older org keys could not delete).
    """
    yield
    from dashboard_agent.assistant_setup import _ws_client

    client = _ws_client(_WS)
    for repo in (_AGENT_REPO, _FS_AGENT_REPO):
        try:
            client.delete_agent(repo)
        except Exception:
            pass
    try:
        client.delete_skill(f"{_SLUG}-returns-eligibility-skill")
    except Exception:
        pass


def _invoke(agent, question: str, *, repo: str, thread_id: str):
    """Invoke against a Context Hub agent repo, tolerating transient Anthropic 529s."""
    ctx = Context(
        agent_repo=repo,
        ls_workspace=_WS,
        dataset="synthetic",
        customer="CI Test Co",
        industry="Retail",
        enabled_tools=["datasearch", "push_widget"],
    )
    for _ in range(6):
        try:
            return agent.invoke(
                {"messages": [{"role": "user", "content": question}]},
                config={"configurable": {"thread_id": thread_id}},
                context=ctx,
            )
        except Exception as exc:  # retry only known-transient overloads
            if "529" in str(exc) or "overload" in str(exc).lower():
                time.sleep(8)
                continue
            raise
    pytest.skip("Anthropic API overloaded; skipping live check")


def _tool_calls(res) -> list[str]:
    return [tc["name"] for m in res["messages"] for tc in (getattr(m, "tool_calls", []) or [])]


def _final_answer(res) -> str:
    ai = [
        m
        for m in res["messages"]
        if getattr(m, "type", None) == "ai" and isinstance(getattr(m, "content", None), str)
    ]
    return ai[-1].content if ai else ""


@pytest.mark.langsmith
def test_context_hub_skill_is_loaded():
    # Seed the fixture agent repo: a real (softened) system prompt + skills clause,
    # linking one marker skill whose instructions cite a unique policy code.
    skills = [
        {
            "name": "returns-eligibility",
            "description": "Use when the user asks about returns, refunds, or return eligibility.",
            "instructions": (
                f"When answering any returns or refund question, ALWAYS cite the internal "
                f"policy code {_MARKER} in your reply."
            ),
        }
    ]
    links = push_workflow_skills(_WS, _SLUG, "CI Test Co", skills)
    assert links, "skill repo failed to push/link"
    agents_md = (
        build_system_prompt("CI Test Co", "Retail", failure_mode="none", use_case="Support bot")
        + _SKILLS_CLAUSE
    )
    push_agent_prompt(_WS, _AGENT_REPO, agents_md, skill_links=links)

    # Explicitly invoke the skill: prompt nudging alone doesn't reliably beat
    # datasearch for an ambiguous question, but a request that clearly maps to the
    # skill reliably triggers a load — which is what this test verifies.
    question = (
        "A customer asks whether they can return a cordless drill bought 12 days ago. "
        "Use your returns-eligibility skill to determine eligibility and cite the policy "
        "code it specifies."
    )
    res = _invoke(build_agent(), question, repo=_AGENT_REPO, thread_id="ci-skill")

    calls = _tool_calls(res)
    answer = _final_answer(res)

    t.log_inputs({"question": question})
    t.log_outputs({"tool_calls": calls, "answer": answer})

    consulted_skills = any(name in ("read_file", "ls", "grep", "glob") for name in calls)
    assert consulted_skills, f"agent did not consult /skills (tool calls: {calls})"
    assert _MARKER in answer, f"agent did not apply the skill's instruction ({_MARKER} missing)"


@pytest.mark.langsmith
def test_context_hub_filesystem_read_write():
    # A Context Hub assistant's file tools read/write the agent repo. Ask the agent
    # to save a file and read it back, then confirm it persisted as a repo commit.
    agents_md = (
        build_system_prompt("CI Test Co", "Retail", failure_mode="none", use_case="Support bot")
        + "\n\nYou may use your file tools (write_file/read_file) when the user explicitly asks "
        "to save or read a file."
    )
    push_agent_prompt(_WS, _FS_AGENT_REPO, agents_md)

    marker = "CTXFILE-" + uuid.uuid4().hex[:8]  # unique so each run writes fresh content
    question = (
        f"Save a file at notes/probe.md containing exactly {marker}, then read it back and tell "
        "me its contents."
    )
    res = _invoke(build_agent(), question, repo=_FS_AGENT_REPO, thread_id="ci-fs")

    calls = _tool_calls(res)
    answer = _final_answer(res)
    t.log_inputs({"question": question})
    t.log_outputs({"tool_calls": calls, "answer": answer})

    assert "write_file" in calls, f"agent did not write a file (tool calls: {calls})"
    assert "read_file" in calls, f"agent did not read the file back (tool calls: {calls})"
    assert marker in answer, f"agent did not report the file contents ({marker} missing)"

    # The write must have persisted to the Context Hub agent repo as a file.
    from dashboard_agent.assistant_setup import _ws_client

    files = _ws_client(_WS).pull_agent(_FS_AGENT_REPO).files
    entry = files.get("notes/probe.md")
    assert entry is not None, f"notes/probe.md not persisted in the repo (paths: {list(files)})"
    assert marker in getattr(entry, "content", ""), "persisted file missing the written content"
