"""Deterministic tests for the assistant-setup assembly (no LLM, no network).

`analyze_customer` (the setup LLM) and the LangSmith `push_*`/`fetch_brand` calls
are mocked, so these exercise `prepare_assistant`'s routing and assembly:
prompt-source branching, tool selection, the failure-mode gap, the skill push,
and the `ls_artifacts` cleanup manifest. Pure and fast — runs in CI, no API key.
"""

import pytest
import yaml

from dashboard_agent import assistant_setup as S


def _analysis(**over):
    """A canned analyze_customer result; override individual keys per test."""
    base = {
        "industry": "Retail",
        "actions": [
            {"label": "A1", "question": "Q1?"},
            {"label": "A2", "question": "Q2?"},
            {"label": "A3", "question": "Q3?"},
        ],
        "enabled_tools": [],
        "skills": [
            {
                "name": "returns-check",
                "description": "Use when a shopper asks about returns.",
                "instructions": "Cite the 30-day window.",
                "example_question": "Can I return this?",
                "action_label": "Shopper: Returns",
                "workflow": "fan-out-and-synthesize",
            }
        ],
        "data_gap": "customer satisfaction scores",
        "gap_action": {"label": "CSAT", "question": "What's our CSAT trend?"},
        "theme": "dark",
    }
    base.update(over)
    return base


@pytest.fixture
def rec(monkeypatch):
    """Mock the LLM + every network push; record what got pushed."""
    calls = {"bundle": None, "agent_prompt": None, "prompt": None}
    monkeypatch.setattr(
        S,
        "fetch_brand",
        lambda *a, **k: {
            "accent": "#111111",
            "accent2": "#222222",
            "accent_scraped": "",
            "logo": "logo.png",
            "fonts": {},
        },
    )

    def _bundle(ws, slug, customer, skills):
        calls["bundle"] = list(skills or [])
        return f"{slug}-skills" if skills else ""

    monkeypatch.setattr(S, "push_skills_bundle", _bundle)

    def _agent(ws, repo, md, skill_links=None):
        calls["agent_prompt"] = {"repo": repo, "md": md, "links": skill_links}
        return "http://agent"

    monkeypatch.setattr(S, "push_agent_prompt", _agent)

    def _prompt(ws, name, text):
        calls["prompt"] = {"name": name, "text": text}
        return "http://prompt"

    monkeypatch.setattr(S, "push_prompt", _prompt)
    return calls


def _prep(monkeypatch, analysis, **payload_over):
    monkeypatch.setattr(S, "analyze_customer", lambda *a, **k: analysis)
    payload = {"workspace": "ws1", "customer": "Acme Co"}
    payload.update(payload_over)
    return S.prepare_assistant(payload)


# --- prompt-source routing (#1, #2) ---


def test_prompt_hub_sets_prompt_name_not_agent_repo(rec, monkeypatch):
    ctx = _prep(monkeypatch, _analysis())["context"]
    assert ctx.get("prompt_name") == "acme-co-system"
    assert "agent_repo" not in ctx
    # Skills are universal — even a Prompt Hub assistant gets a skills bundle mounted.
    assert ctx.get("skills_repo") == "acme-co-skills"
    assert rec["prompt"] is not None
    assert rec["agent_prompt"] is None
    # ...and its prompt carries the skills clause (so the mounted skills get used).
    assert "SKILLS (IMPORTANT)" in rec["prompt"]["text"]


def test_context_hub_sets_agent_repo_and_points_at_skill(rec, monkeypatch):
    out = _prep(monkeypatch, _analysis(), prompt_source="context_hub")
    ctx = out["context"]
    assert ctx.get("agent_repo") == "acme-co-agent"
    assert "prompt_name" not in ctx
    assert ctx.get("skills_repo") == "acme-co-skills"  # skills come from the bundle, not the repo
    md = rec["agent_prompt"]["md"]
    # Dashboard workflow is a pointer to the skill, not inlined; skills clause present.
    assert "read your `dashboard` skill" in md
    assert "SKILLS (IMPORTANT)" in md


# --- skills are universal: same bundle regardless of prompt storage (#8) ---


def test_skills_bundle_is_universal_dashboard_and_llm_skills(rec, monkeypatch):
    # Default prompt_source is Prompt Hub — the bundle must STILL be pushed.
    _prep(monkeypatch, _analysis())
    names = [s["name"] for s in rec["bundle"]]
    assert names[0] == "dashboard"  # curated dashboard skill prepended
    assert "returns-check" in names  # plus the LLM's workflow skill


# --- cleanup manifest (#3) ---


def test_metadata_records_ls_artifacts_manifest(rec, monkeypatch):
    art = _prep(monkeypatch, _analysis(), prompt_source="context_hub")["metadata"]["ls_artifacts"]
    assert art["workspace"] == "ws1"
    assert art["project"] == "Acme Co"  # ls_project == customer name
    assert art["agent_repo"] == "acme-co-agent"
    assert art["skills_repo"] == "acme-co-skills"  # bundle repo, deleted via delete_agent
    assert art["skills"] == []  # legacy per-skill list, unused now
    # Every artifact the /cleanup cascade deletes has to have a slot here, or it leaks
    # into the customer's workspace. These two are the attached evaluator and the
    # Prompt Hub prompt holding its judge.
    assert "eval_rule_id" in art
    assert "eval_judge_prompt" in art


def test_judge_prompt_is_recorded_only_when_the_evaluator_attached(rec, monkeypatch):
    """No rule means no judge prompt to delete, and a blank keeps /cleanup quiet.

    `_try` no-ops on a falsy handle, so recording a name for an assistant that never got
    an evaluator would put a spurious 404 in every cleanup report.
    """
    from dashboard_agent import assistant_evals as AE

    monkeypatch.setattr(AE, "ensure_eval_dataset", lambda *a, **k: "acme-ds")
    monkeypatch.setattr(AE, "ensure_dataset_evaluator", lambda *a, **k: "")
    art = _prep(monkeypatch, _analysis())["metadata"]["ls_artifacts"]
    assert art["eval_rule_id"] == ""
    assert art["eval_judge_prompt"] == ""

    monkeypatch.setattr(AE, "ensure_dataset_evaluator", lambda *a, **k: "rule-7")
    art = _prep(monkeypatch, _analysis())["metadata"]["ls_artifacts"]
    assert art["eval_rule_id"] == "rule-7"
    assert art["eval_judge_prompt"] == AE.judge_prompt_name("acme-ds")


# --- tool selection (#4) ---


def test_enabled_tools_intersect_catalogue_union_defaults(rec, monkeypatch):
    tools = set(
        _prep(monkeypatch, _analysis(enabled_tools=["web_search", "bogus_tool"]))["context"][
            "enabled_tools"
        ]
    )
    assert "web_search" in tools  # valid pick kept
    assert "bogus_tool" not in tools  # unknown id dropped
    assert {"datasearch", "push_widget"} <= tools  # defaults always present


def test_setup_never_auto_enables_explicit_only_tools(rec, monkeypatch):
    # list_data_sources is explicit-only: even if the LLM lists it, setup drops it
    # (the user must turn it on themselves in settings).
    tools = set(
        _prep(monkeypatch, _analysis(enabled_tools=["web_search", "list_data_sources"]))["context"][
            "enabled_tools"
        ]
    )
    assert "list_data_sources" not in tools  # explicit-only, never auto-enabled
    assert "web_search" in tools  # a normal optional pick is still kept


# --- failure mode / planted gap (#5) ---


def test_hallucination_plants_gap_and_orders_gap_action_last(rec, monkeypatch):
    out = _prep(monkeypatch, _analysis(), failure_mode="hallucination")
    ctx, actions = out["context"], out["metadata"]["actions"]
    assert ctx["data_gap"] == "customer satisfaction scores"
    assert out["metadata"]["failure_mode"] == "hallucination"
    assert actions[-1]["question"] == "What's our CSAT trend?"  # gap probe last
    assert len(actions) <= 3


def test_no_failure_mode_has_no_gap_and_three_grounded_actions(rec, monkeypatch):
    out = _prep(monkeypatch, _analysis(), failure_mode="none")
    assert "data_gap" not in out["context"]
    assert len(out["metadata"]["actions"]) == 3


# --- every quick-action label reads as '<Persona>: <gist>' ---


def test_all_quick_action_labels_are_persona_formatted(rec, monkeypatch):
    actions = _prep(monkeypatch, _analysis())["metadata"]["actions"]
    assert actions
    assert all(":" in a["label"] for a in actions)  # persona chip is consistent


def test_skill_action_uses_llm_action_label(rec, monkeypatch):
    actions = _prep(monkeypatch, _analysis())["metadata"]["actions"]
    assert any(a["label"] == "Shopper: Returns" for a in actions)  # LLM label used verbatim


def test_label_without_persona_is_normalized(rec, monkeypatch):
    # A skill lacking action_label falls back to its name and still gets a persona
    # prefix, so the format holds no matter what the LLM returned.
    a = _analysis(
        skills=[
            {
                "name": "stock-lookup",
                "description": "d",
                "instructions": "i",
                "example_question": "in stock?",
            }
        ]
    )
    labels = [x["label"] for x in _prep(monkeypatch, a)["metadata"]["actions"]]
    assert all(":" in label for label in labels)
    assert "Customer: Stock Lookup" in labels


# --- deterministic no-em-dash rule (#6) ---


def test_demo_brief_has_no_em_dash():
    d = S.build_demo_brief(
        "Acme Co",
        "weekly revenue review",
        [{"label": "a", "question": "q?"}],
        ["datasearch", "push_widget"],
        "hallucination",
        "customer satisfaction scores",
    )
    assert all("—" not in line for line in d["brief"] + d["flow"])


# --- dynamic-subagent workflow pattern woven into skills ---


def test_skill_md_appends_known_workflow_pattern():
    md = S._skill_md(
        "triage", "Use when triaging tickets", "Do the steps.", "fan-out-and-synthesize"
    )
    assert "## Workflow: fan-out-and-synthesize" in md
    assert "task()" in md  # tells the agent to orchestrate via the interpreter
    assert "Do the steps." in md  # original instructions preserved


def test_skill_md_omits_workflow_when_empty_or_unknown():
    assert "## Workflow" not in S._skill_md("s", "d", "body", "")
    assert "## Workflow" not in S._skill_md("s", "d", "body", "not-a-pattern")


def test_workflow_flows_from_analysis_into_pushed_skill(rec, monkeypatch):
    _prep(monkeypatch, _analysis())
    names_workflows = {s["name"]: s.get("workflow") for s in rec["bundle"]}
    assert names_workflows.get("returns-check") == "fan-out-and-synthesize"


# --- SKILL.md frontmatter survives a colon in the description (#13) ---


def test_skill_md_frontmatter_is_valid_yaml_with_colon_description():
    md = S._skill_md("returns-check", "Use when: a shopper asks about returns", "do X")
    meta = yaml.safe_load(md.split("---")[1])
    assert meta["name"] == "returns-check"  # name == mount dir
    assert "returns" in meta["description"]  # colon didn't truncate/break it


# --- the automatic demo-traffic backfill ---


@pytest.fixture(autouse=True)
def traffic(monkeypatch):
    """Capture the backfill instead of spawning it.

    Autouse because EVERY `prepare_assistant` call in this file would otherwise spawn
    a real backfill thread — several live agent runs and a few thousand LangSmith
    ingests — which contradicts this module's no-network contract and leaves threads
    racing the rest of the suite. Tests that assert on it just request the fixture.
    """
    from dashboard_agent import demo_traffic as DT

    started: list[tuple] = []
    monkeypatch.setattr(
        DT, "start_demo_traffic", lambda ws, project, **kw: started.append((ws, project, kw))
    )
    return started


def test_setup_starts_the_backfill_in_the_assistants_own_trace_project(rec, monkeypatch, traffic):
    """Traffic goes through start_demo_traffic, so the panel and Generate can see it.

    Spawned bare, the setup backfill was invisible to `POST /demo-traffic`: the panel
    showed the pre-backfill empty state while it ran, and Generate would start a second
    one on top of it.
    """
    out = _prep(monkeypatch, _analysis(), failure_mode="hallucination")
    assert len(traffic) == 1
    workspace, project, kwargs = traffic[0]
    assert workspace == "ws1"
    assert project == out["context"]["ls_project"]
    # The gap probe is what Insights clusters on, so it has to reach the backfill.
    assert kwargs["data_gap"] == out["context"]["data_gap"]
    assert kwargs["customer"] == "Acme Co"
    assert len(kwargs["actions"]) == 3


def test_no_push_means_no_backfill(rec, monkeypatch, traffic):
    # push_prompts=False is the dry-run setup: no prompt, no dataset, and no traffic
    # (a project nothing was pushed to is not the one the demo will use).
    _prep(monkeypatch, _analysis(), push_prompts=False)
    assert traffic == []
