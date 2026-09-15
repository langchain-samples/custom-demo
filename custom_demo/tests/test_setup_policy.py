"""Characterize setup policy and effect ordering without external services."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_demo.provisioning import setup as S


@pytest.fixture
def setup_case(monkeypatch):
    analysis = {
        "industry": "Retail",
        "actions": [
            {"label": "Buyer: Stock", "question": "Stock?"},
            {"label": "Manager: Sales", "question": "Sales?"},
            {"label": "Finance: Margin", "question": "Margin?"},
        ],
        "skills": [],
        "enabled_tools": ["web_search"],
        "seed_files": [{"name": "stock.csv", "kind": "csv", "rows": [["1"]]}],
        "data_gap": "satisfaction scores",
        "gap_action": {"label": "CSAT", "question": "CSAT?", "extra": "keep"},
    }
    brand = {
        "accent": "#111111",
        "accent2": "#222222",
        "accent_scraped": "#333333",
        "logo": "logo.png",
        "fonts": {},
    }
    events = []

    def record(name, result):
        def call(*args, **kwargs):
            events.append((name, deepcopy(args), deepcopy(kwargs)))
            return result

        return call

    class Thread:
        def __init__(self, *, target, kwargs=None, daemon):
            self.name = "prewarm" if target is S.prewarm_sandbox else "tag"
            self.kwargs = kwargs or {}
            assert daemon is True

        def start(self):
            events.append((self.name, (), deepcopy(self.kwargs)))

    monkeypatch.setattr(S, "threading", SimpleNamespace(Thread=Thread))
    monkeypatch.setattr(S.secrets, "token_hex", lambda n: "abcdef")
    monkeypatch.setattr(S, "fetch_brand", lambda *a, **kw: deepcopy(brand))
    monkeypatch.setattr(S, "analyze_customer", lambda *a, **kw: deepcopy(analysis))
    monkeypatch.setattr(S, "push_skills_bundle", record("skills", "acme-co-skills"))
    monkeypatch.setattr(S, "push_agent_prompt", record("prompt", "https://example.test/prompt"))
    monkeypatch.setattr(S, "ensure_eval_dataset", record("dataset", "acme-dataset"))
    monkeypatch.setattr(
        S,
        "ensure_dataset_evaluator",
        record("evaluator", {"rule_id": "rule-1", "evaluator_id": "eval-1", "error": ""}),
    )
    monkeypatch.setattr(S, "start_demo_traffic", record("traffic", None))

    def prepare(**overrides):
        return S.prepare_assistant(
            {"workspace": "ws1", "customer": " Acme Co ", "owner": "Jo", **overrides}
        )

    return SimpleNamespace(analysis=analysis, brand=brand, events=events, prepare=prepare)


@pytest.mark.parametrize("mode", ["none", "hallucination"])
@pytest.mark.parametrize("push", [False, True])
def test_complete_setup_payload_prompt_and_effect_order(setup_case, mode, push):
    case = setup_case
    out = case.prepare(failure_mode=mode, push_prompts=push, demo_traffic=True)
    actions = deepcopy(case.analysis["actions"])
    gap = ""
    if mode == "hallucination":
        gap = "satisfaction scores"
        actions = actions[:2] + [
            {"label": "Customer: CSAT", "question": "CSAT?", "extra": "keep", "kind": "gap"}
        ]

    context = {
        "ls_workspace": "ws1",
        "customer": "Acme Co",
        "ls_project": "Acme Co",
        "industry": "Retail",
        "enabled_tools": ["ask_user", "push_widget", "web_search"],
        "sandbox_seed": case.analysis["seed_files"],
        "sandbox_key": "acme-co-abcdef",
    }
    if push:
        context.update(skills_repo="acme-co-skills", agent_repo="acme-co-agent")

    demo = S.build_demo_brief("Acme Co", "", actions, context["enabled_tools"], mode, gap)
    metadata = {
        "owner_name": "Jo",
        "customer": "Acme Co",
        "industry": "Retail",
        "display_name": "Acme Co GPT",
        "accent": "#111111",
        "accent2": "#222222",
        "brand_neutral": "",
        "brand_tint": 6,
        "logo": "logo.png",
        "actions": actions,
        "theme": "dark",
        "font_heading": "",
        "font_body": "",
        "font_heading_fallback": "Geist Variable",
        "font_body_fallback": "Geist Variable",
        "font_source": "google",
        "failure_mode": mode,
        "voice": {},
        "ls_artifacts": {
            "workspace": "ws1",
            "project": "Acme Co",
            "agent_repo": "acme-co-agent" if push else "",
            "skills_repo": "acme-co-skills" if push else "",
            "skills": [],
            "eval_dataset": "acme-dataset" if push else "",
            "eval_rule_id": "rule-1" if push else "",
            "eval_evaluator_id": "eval-1" if push else "",
            "eval_judge_prompt": S.judge_prompt_name("acme-dataset") if push else "",
            "annotation_queue": S.annotation_queue_name("Acme Co"),
        },
        "demo_brief": demo["brief"],
        "demo_flow": demo["flow"],
    }
    assert out == {
        "name": "Acme Co",
        "display_name": "Acme Co GPT",
        "accent": "#111111",
        "accent2": "#222222",
        "logo": "logo.png",
        "actions": actions,
        "metadata": metadata,
        "context": context,
        "prompt_urls": {"system": "https://example.test/prompt"} if push else {},
    }
    if not push:
        assert case.events == []
        return

    assert [e[0] for e in case.events] == [
        "skills",
        "prompt",
        "prewarm",
        "dataset",
        "evaluator",
        "tag",
        "traffic",
    ]
    assert case.events[0] == ("skills", ("ws1", "acme-co", "Acme Co", [S.DASHBOARD_SKILL]), {})
    assert case.events[1] == (
        "prompt",
        (
            "ws1",
            "acme-co-agent",
            S.build_system_prompt(
                "Acme Co", "Retail", failure_mode=mode, use_case="", dashboard="skill"
            )
            + S._SKILLS_CLAUSE,
        ),
        {},
    )
    assert case.events[2][2] == {
        "sandbox_key": "acme-co-abcdef",
        "agent_repo": "acme-co-agent",
        "customer": "Acme Co",
        "seed": case.analysis["seed_files"],
    }
    assert case.events[3] == ("dataset", ("ws1", "Acme Co", mode, actions, gap), {})
    assert case.events[4] == ("evaluator", ("ws1", "acme-dataset", "Acme Co"), {})
    assert case.events[-1] == (
        "traffic",
        ("ws1", "Acme Co"),
        {"context": context, "actions": actions, "data_gap": gap, "customer": "Acme Co"},
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, ["ask_user", "push_widget", "web_search"]),
        ({"enabled_tools": None}, ["ask_user", "push_widget", "web_search"]),
        ({"enabled_tools": []}, ["ask_user", "push_widget", "web_search"]),
        ({"enabled_tools": ["draft_email"]}, ["ask_user", "draft_email", "push_widget"]),
        ({"enabled_tools": ["bogus"]}, ["ask_user", "push_widget"]),
        ({"enabled_tools": [" web_search "]}, ["ask_user", "push_widget"]),
        (
            {"enabled_tools": ["web_search", "web_search", "bogus"]},
            ["ask_user", "push_widget", "web_search"],
        ),
    ],
)
def test_tool_override_policy(setup_case, override, expected):
    out = setup_case.prepare(push_prompts=False, **override)
    assert out["context"]["enabled_tools"] == expected


def test_explicit_only_tools_are_filtered_only_from_analysis(setup_case, monkeypatch):
    monkeypatch.setattr(S, "EXPLICIT_ONLY", {"web_search"})
    assert setup_case.prepare(push_prompts=False)["context"]["enabled_tools"] == [
        "ask_user",
        "push_widget",
    ]
    assert setup_case.prepare(push_prompts=False, enabled_tools=["web_search"])["context"][
        "enabled_tools"
    ] == ["ask_user", "push_widget", "web_search"]


def test_skill_questions_lead_caller_actions_without_mutating_analysis(setup_case):
    case = setup_case
    case.analysis["skills"] = [
        {"name": "ignored"},
        {"name": "stock-check", "example_question": "Check?"},
        {"name": "returns", "example_question": "Return?", "action_label": "Shopper: Returns"},
    ]
    original = deepcopy(case.analysis)
    out = case.prepare(
        push_prompts=False, actions=[{"label": " Mine ", "question": "Mine?", "x": 1}]
    )
    assert out["actions"] == [
        {"label": "Customer: Stock Check", "question": "Check?"},
        {"label": "Shopper: Returns", "question": "Return?"},
        {"label": "Customer: Mine", "question": "Mine?", "x": 1},
    ]
    assert case.analysis == original


@pytest.mark.parametrize("count", [0, 1, 2, 3])
@pytest.mark.parametrize("gap_action", [None, {"label": "Empty"}, {"question": "Missing?"}])
def test_thin_gap_analysis(setup_case, count, gap_action):
    case = setup_case
    case.analysis["actions"] = case.analysis["actions"][:count]
    case.analysis["gap_action"] = gap_action
    case.analysis["data_gap"] = ""
    out = case.prepare(hallucination=True)
    expected = case.analysis["actions"][:2]
    if gap_action and gap_action.get("question"):
        expected = expected + [{"question": "Missing?", "label": "Customer: Ask", "kind": "gap"}]

    assert out["actions"] == expected
    assert case.events[3][1][-1] == "year-over-year figures by segment"


@pytest.mark.parametrize(
    ("brand_values", "analysis_values", "expected"),
    [
        ({}, {"primary_color": "#444444", "secondary_color": "#555555"}, ("#111111", "#222222")),
        (
            {"accent": "", "accent2": ""},
            {"primary_color": "#444444", "secondary_color": "#555555"},
            ("#444444", "#555555"),
        ),
        ({"accent": "", "accent2": ""}, {}, ("#333333", "")),
        ({"accent": "", "accent2": "", "accent_scraped": ""}, {}, ("#0072BC", "")),
    ],
)
def test_brand_color_precedence(setup_case, brand_values, analysis_values, expected):
    setup_case.brand.update(brand_values)
    setup_case.analysis.update(analysis_values)
    out = setup_case.prepare(push_prompts=False)
    assert (out["accent"], out["accent2"]) == expected
    assert (out["metadata"]["accent"], out["metadata"]["accent2"]) == expected
    assert out["metadata"]["brand_neutral"] == ""


@pytest.mark.parametrize("fonts", [{}, {"heading": "Brand Heading"}, {"body": "Brand Body"}])
def test_font_and_display_precedence(setup_case, fonts):
    setup_case.brand["fonts"] = fonts
    setup_case.analysis.update(
        heading_font="Analysis Heading",
        body_font="Analysis Body",
        heading_fallback="Inter Variable",
        body_fallback="",
        theme="light",
    )
    metadata = setup_case.prepare(
        push_prompts=False, display_name="My assistant", industry="Custom"
    )["metadata"]
    assert metadata["font_heading"] == fonts.get("heading", "Analysis Heading")
    assert metadata["font_body"] == fonts.get("body", "Analysis Body")
    assert metadata["font_heading_fallback"] == "Inter Variable"
    assert metadata["font_body_fallback"] == "Geist Variable"
    assert metadata["display_name"] == "My assistant"
    assert metadata["industry"] == "Custom"
    assert metadata["theme"] == "light"


@pytest.mark.parametrize("payload", [{}, {"customer": "Acme"}])
def test_missing_workspace_refuses_before_discovery(monkeypatch, payload):
    def unexpected(*args, **kwargs):
        pytest.fail("missing workspace must fail before discovery")

    monkeypatch.setattr(S, "ThreadPoolExecutor", unexpected)
    with pytest.raises(KeyError, match="workspace"):
        S.prepare_assistant(payload)


def test_missing_seed_refuses_before_resource_creation(setup_case):
    setup_case.analysis["seed_files"] = []
    with pytest.raises(S.SeedSpecError, match="Nothing was created"):
        setup_case.prepare()

    assert setup_case.events == []


def test_unknown_failure_mode_keeps_grounded_actions(setup_case):
    out = setup_case.prepare(failure_mode="unknown")
    assert out["actions"] == setup_case.analysis["actions"]
    assert out["metadata"]["failure_mode"] == "unknown"
    assert setup_case.events[3][1][-1] == ""


def test_demo_plan_resolves_without_external_effects_or_input_mutation(setup_case, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("planning must not perform discovery, randomness, or external effects")

    for name in ("ThreadPoolExecutor", "fetch_brand", "analyze_customer", "_ws_client"):
        monkeypatch.setattr(S, name, unexpected)

    monkeypatch.setattr(S.secrets, "token_hex", unexpected)
    case = setup_case
    original = deepcopy((case.brand, case.analysis))
    plan = S.plan_demo(
        {"workspace": "ws1", "customer": " Acme Co ", "failure_mode": "hallucination"},
        case.brand,
        case.analysis,
        sandbox_suffix="abcdef",
    )
    assert case.events == []
    assert (case.brand, case.analysis) == original
    assert plan.customer == "Acme Co"
    assert plan.agent_repo == "acme-co-agent"
    assert plan.sandbox_key == "acme-co-abcdef"
    assert plan.planted_gap == "satisfaction scores"
    assert plan.actions[-1]["kind"] == "gap"
    assert plan.skills == [S.DASHBOARD_SKILL]
    assert plan.seed_files == case.analysis["seed_files"]
    assert plan.runtime_context()["enabled_tools"] == plan.enabled_tools


def test_one_plan_drives_eval_traffic_brief_and_saved_definition(setup_case, monkeypatch):
    captured = []
    build_plan = S.plan_demo
    build_brief = S.build_demo_brief
    brief_inputs = []

    def plan(*args, **kwargs):
        result = build_plan(*args, **kwargs)
        captured.append(result)
        return result

    def brief(*args):
        brief_inputs.append(args)
        return build_brief(*args)

    monkeypatch.setattr(S, "plan_demo", plan)
    monkeypatch.setattr(S, "build_demo_brief", brief)
    out = setup_case.prepare(failure_mode="hallucination", demo_traffic=True)
    assert len(captured) == 1
    plan = captured[0]
    assert out["actions"] is plan.actions
    assert out["metadata"]["actions"] is plan.actions
    assert out["context"]["enabled_tools"] is plan.enabled_tools
    assert out["context"]["sandbox_seed"] is plan.seed_files
    assert brief_inputs == [
        (
            plan.customer,
            plan.use_case,
            plan.actions,
            plan.enabled_tools,
            plan.failure_mode,
            plan.planted_gap,
        )
    ]
    dataset = next(event for event in setup_case.events if event[0] == "dataset")
    traffic = next(event for event in setup_case.events if event[0] == "traffic")
    assert dataset[1] == (
        plan.workspace,
        plan.customer,
        plan.failure_mode,
        plan.actions,
        plan.planted_gap,
    )
    assert traffic[2]["actions"] == plan.actions
    assert traffic[2]["data_gap"] == plan.planted_gap


def test_failed_skills_push_does_not_advertise_a_mounted_bundle(setup_case, monkeypatch):
    monkeypatch.setattr(S, "push_skills_bundle", lambda *args: "")
    out = setup_case.prepare()
    prompt = next(event for event in setup_case.events if event[0] == "prompt")
    assert prompt[1][2] == S.build_system_prompt(
        "Acme Co", "Retail", failure_mode="none", use_case="", dashboard="skill"
    )
    assert "skills_repo" not in out["context"]
    assert out["metadata"]["ls_artifacts"]["skills_repo"] == ""
