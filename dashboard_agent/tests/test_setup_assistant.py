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
    calls = {"skills": None, "agent_prompt": None, "prompt": None}
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

    def _skills(ws, slug, customer, skills):
        calls["skills"] = list(skills or [])
        return {
            f"skills/{s['name']}": f"{slug}-{s['name']}-skill"
            for s in (skills or [])
            if s.get("name")
        }

    monkeypatch.setattr(S, "push_workflow_skills", _skills)

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
    assert rec["prompt"] is not None
    assert rec["agent_prompt"] is None


def test_context_hub_sets_agent_repo_and_points_at_skill(rec, monkeypatch):
    out = _prep(monkeypatch, _analysis(), prompt_source="context_hub")
    ctx = out["context"]
    assert ctx.get("agent_repo") == "acme-co-agent"
    assert "prompt_name" not in ctx
    md = rec["agent_prompt"]["md"]
    # Dashboard workflow is a pointer to the skill, not inlined; skills clause present.
    assert "read your `dashboard` skill" in md
    assert "SKILLS (IMPORTANT)" in md


# --- dashboard skill pushed for Context Hub (#8) ---


def test_context_hub_pushes_dashboard_and_llm_skills(rec, monkeypatch):
    _prep(monkeypatch, _analysis(), prompt_source="context_hub")
    names = [s["name"] for s in rec["skills"]]
    assert names[0] == "dashboard"  # curated dashboard skill prepended
    assert "returns-check" in names  # plus the LLM's workflow skill


# --- cleanup manifest (#3) ---


def test_metadata_records_ls_artifacts_manifest(rec, monkeypatch):
    art = _prep(monkeypatch, _analysis(), prompt_source="context_hub")["metadata"]["ls_artifacts"]
    assert art["workspace"] == "ws1"
    assert art["project"] == "Acme Co"  # ls_project == customer name
    assert art["agent_repo"] == "acme-co-agent"
    assert "acme-co-dashboard-skill" in art["skills"]
    assert "acme-co-returns-check-skill" in art["skills"]


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


# --- SKILL.md frontmatter survives a colon in the description (#13) ---


def test_skill_md_frontmatter_is_valid_yaml_with_colon_description():
    md = S._skill_md("returns-check", "Use when: a shopper asks about returns", "do X")
    meta = yaml.safe_load(md.split("---")[1])
    assert meta["name"] == "returns-check"  # name == mount dir
    assert "returns" in meta["description"]  # colon didn't truncate/break it
