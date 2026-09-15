"""Deterministic tests for the assistant-setup assembly (no LLM, no network).

`analyze_customer` (the setup LLM) and the LangSmith `push_*`/`fetch_brand` calls
are mocked, so these exercise `prepare_assistant`'s routing and assembly:
prompt-source branching, tool selection, the failure-mode gap, the skill push,
and the `ls_artifacts` cleanup manifest. Pure and fast — runs in CI, no API key.
"""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from langsmith.utils import LangSmithConflictError

from custom_demo import setup_graph
from custom_demo.provisioning import setup as S
from custom_demo.setup_graph import _INPUT_KEYS, SetupState

# One valid skill, enough for a push to be attempted.
_SKILL = {
    "name": "returns-check",
    "description": "Use when a shopper asks about returns.",
    "instructions": "Cite the 30-day window.",
}


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
                "sandbox_step": "Load returns.csv and rate by SKU",
            }
        ],
        "data_gap": "customer satisfaction scores",
        "gap_action": {"label": "CSAT", "question": "What's our CSAT trend?"},
        "theme": "dark",
        # The assistant's starting data. Not optional: `prepare_assistant` refuses to
        # build on an analysis that produced none, because there is no generic dataset
        # to stand in for them.
        "seed_files": [
            {
                "name": "returns.csv",
                "kind": "csv",
                "description": "Returns by SKU",
                "columns": ["sku", "returned"],
                "rows": [["A-1", "3"]],
            }
        ],
    }
    base.update(over)
    return base


@pytest.fixture
def rec(monkeypatch):
    """Mock external effects and finish background work before fixture teardown."""
    effects = {
        "prewarm_sandbox": Mock(),
        "ensure_eval_dataset": Mock(return_value="acme-ds"),
        "ensure_dataset_evaluator": Mock(
            return_value={"rule_id": "", "evaluator_id": "", "error": ""}
        ),
        "tag_assistant_resources": Mock(return_value={}),
    }
    for name, effect in effects.items():
        monkeypatch.setattr(S, name, effect)

    class InlineThread:
        def __init__(self, *, target, kwargs=None, daemon):
            assert daemon is True
            self.target = target
            self.kwargs = kwargs or {}

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr(S, "threading", SimpleNamespace(Thread=InlineThread))
    calls: dict = {"bundle": None, "agent_prompt": None, "effects": effects}
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

    return calls


def _prep(monkeypatch, analysis, **payload_over):
    monkeypatch.setattr(S, "analyze_customer", lambda *a, **k: analysis)
    payload = {"workspace": "ws1", "customer": "Acme Co"}
    payload.update(payload_over)
    return S.prepare_assistant(payload)


def test_setup_fixture_contains_every_external_effect(rec, monkeypatch):
    assert S.threading is not threading
    caller_thread = threading.get_ident()
    tagged_on = []
    effects = rec["effects"]
    effects["tag_assistant_resources"].side_effect = lambda **kwargs: tagged_on.append(
        threading.get_ident()
    )

    out = _prep(monkeypatch, _analysis())

    effects["prewarm_sandbox"].assert_called_once_with(
        sandbox_key=out["context"]["sandbox_key"],
        agent_repo="acme-co-agent",
        customer="Acme Co",
        seed=out["context"]["sandbox_seed"],
    )
    effects["ensure_eval_dataset"].assert_called_once()
    effects["ensure_dataset_evaluator"].assert_called_once()
    effects["tag_assistant_resources"].assert_called_once()
    assert tagged_on == [caller_thread]


def test_tagging_uses_the_same_handles_as_cleanup(rec, monkeypatch):
    targets = []
    monkeypatch.setattr(
        S, "tag_assistant_resources", lambda *, api_key, **kwargs: targets.append(kwargs)
    )
    out = _prep(monkeypatch, _analysis())
    artifacts = out["metadata"]["ls_artifacts"]
    assert targets == [
        {
            "customer": "Acme Co",
            "workspace": artifacts["workspace"],
            "project": artifacts["project"],
            "dataset": artifacts["eval_dataset"],
            "prompts": (),
            "agents": (artifacts["agent_repo"], artifacts["skills_repo"]),
            "evaluator_id": artifacts["eval_evaluator_id"],
        }
    ]


# --- prompt storage ---


def test_the_prompt_goes_to_a_context_hub_agent_repo(rec, monkeypatch):
    """One storage location, so "edit the prompt" means one thing to a presenter."""
    ctx = _prep(monkeypatch, _analysis())["context"]
    assert ctx.get("agent_repo") == "acme-co-agent"
    # No second prompt location: an inline `prompt` or a registry `prompt_name`
    # would each be another place the live edit could fail to take effect.
    assert "prompt_name" not in ctx
    assert "prompt" not in ctx
    assert ctx.get("skills_repo") == "acme-co-skills"  # skills come from the bundle, not the repo
    md = rec["agent_prompt"]["md"]
    # Dashboard workflow is a pointer to the skill, not inlined; skills clause present.
    assert "read your `dashboard` skill" in md
    assert "SKILLS (IMPORTANT)" in md


# --- skills are universal (#8) ---


def test_skills_bundle_is_universal_dashboard_and_llm_skills(rec, monkeypatch):
    _prep(monkeypatch, _analysis())
    names = [s["name"] for s in rec["bundle"]]
    assert names[0] == "dashboard"  # curated dashboard skill prepended
    assert "returns-check" in names  # plus the LLM's workflow skill


# --- cleanup manifest (#3) ---


def test_metadata_records_ls_artifacts_manifest(rec, monkeypatch):
    art = _prep(monkeypatch, _analysis())["metadata"]["ls_artifacts"]
    assert art["workspace"] == "ws1"
    assert art["project"] == "Acme Co"  # ls_project == customer name
    assert art["agent_repo"] == "acme-co-agent"
    assert art["skills_repo"] == "acme-co-skills"  # bundle repo, deleted via delete_agent
    assert art["skills"] == []  # legacy per-skill list; nothing populates it
    # Every artifact the /cleanup cascade deletes has to have a slot here, or it leaks
    # into the customer's workspace. These two are the attached evaluator and the
    # prompt-registry prompt holding its judge.
    assert "eval_rule_id" in art
    assert "eval_judge_prompt" in art


def test_judge_prompt_is_recorded_only_when_the_evaluator_attached(rec, monkeypatch):
    """No rule means no judge prompt to delete, and a blank keeps /cleanup quiet.

    `_try` no-ops on a falsy handle, so recording a name for an assistant that never got
    an evaluator would put a spurious 404 in every cleanup report.
    """
    # Patched on setup.py, which is where `prepare_assistant` looks these up.
    monkeypatch.setattr(S, "ensure_eval_dataset", lambda *a, **k: "acme-ds")
    monkeypatch.setattr(
        S,
        "ensure_dataset_evaluator",
        lambda *a, **k: {"rule_id": "", "evaluator_id": "", "error": "503"},
    )
    art = _prep(monkeypatch, _analysis())["metadata"]["ls_artifacts"]
    assert art["eval_rule_id"] == ""
    assert art["eval_evaluator_id"] == ""
    assert art["eval_judge_prompt"] == ""

    monkeypatch.setattr(
        S,
        "ensure_dataset_evaluator",
        lambda *a, **k: {"rule_id": "rule-7", "evaluator_id": "ev-7", "error": ""},
    )
    art = _prep(monkeypatch, _analysis())["metadata"]["ls_artifacts"]
    assert art["eval_rule_id"] == "rule-7"
    # The evaluator is a separate object from the rule; /cleanup needs both ids or it
    # leaves a row on the customer's Evaluators page forever.
    assert art["eval_evaluator_id"] == "ev-7"
    assert art["eval_judge_prompt"] == S.judge_prompt_name("acme-ds")


# --- tool selection (#4) ---


def test_enabled_tools_intersect_catalogue_union_defaults(rec, monkeypatch):
    tools = set(
        _prep(monkeypatch, _analysis(enabled_tools=["web_search", "bogus_tool"]))["context"][
            "enabled_tools"
        ]
    )
    assert "web_search" in tools  # valid pick kept
    assert "bogus_tool" not in tools  # unknown id dropped
    assert {"push_widget"} <= tools  # defaults always present


def test_setup_never_auto_enables_explicit_only_tools(rec, monkeypatch):
    """The setup LLM's pick is honoured, minus anything marked explicit-only.

    The rule exists because of `list_data_sources`, which the LLM added to almost
    every assistant whether the scenario called for it or not. No catalogue tool is
    marked explicit-only today (`EXPLICIT_ONLY` is empty), so what this pins is the
    surviving half of the rule: a normal optional pick IS kept.
    """
    tools = _prep(monkeypatch, _analysis(enabled_tools=["web_search"]))["context"]["enabled_tools"]
    assert "web_search" in tools


# --- failure mode / planted gap (#5) ---


def test_hallucination_plants_gap_and_orders_gap_action_last(rec, monkeypatch):
    out = _prep(monkeypatch, _analysis(), failure_mode="hallucination")
    ctx, actions = out["context"], out["metadata"]["actions"]
    # The gap is not written onto the assistant's context: it is what the seeded
    # files omit, and only the probe action and the eval example need it.
    assert "data_gap" not in ctx
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
        ["web_search", "push_widget"],
        "hallucination",
        "customer satisfaction scores",
    )
    assert all("—" not in line for line in d["brief"] + d["flow"])


# --- dynamic subagents + the code sandbox, woven into every skill ---


def test_skill_md_appends_known_workflow_pattern():
    md = S._skill_md(
        "triage", "Use when triaging tickets", "Do the steps.", "fan-out-and-synthesize"
    )
    assert "## Workflow: fan-out-and-synthesize" in md
    assert "task()" in md  # tells the agent to orchestrate via the interpreter
    assert "Do the steps." in md  # original instructions preserved


def test_skill_md_falls_back_to_a_pattern_when_empty_or_unknown():
    """A skill with no fan-out demos nothing, so an unset pattern gets the default."""
    for workflow in ("", "not-a-pattern"):
        md = S._skill_md("s", "d", "body", workflow)
        assert f"## Workflow: {S._DEFAULT_WORKFLOW}" in md
        assert "task()" in md


def test_skill_md_always_points_the_skill_at_the_sandbox():
    md = S._skill_md("triage", "d", "body", "tournament", "Load claims.csv and rank denials")
    assert "## Data: compute it in the sandbox" in md
    assert "Load claims.csv and rank denials" in md  # the model's own concrete step
    assert "/workspace/data" in md and "`execute`" in md


def test_sandbox_section_survives_a_missing_step():
    md = S._skill_md("triage", "d", "body", "tournament", "")
    assert "## Data: compute it in the sandbox" in md
    assert "/workspace/data" in md


def test_workflow_and_sandbox_step_flow_from_analysis_into_pushed_skill(rec, monkeypatch):
    _prep(monkeypatch, _analysis())
    pushed = {s["name"]: s for s in rec["bundle"]}
    assert pushed["returns-check"]["workflow"] == "fan-out-and-synthesize"
    assert pushed["returns-check"]["sandbox_step"] == "Load returns.csv and rate by SKU"


# --- the VM's seed files reach the assistant ---


def test_seed_files_survive_the_llm_call_into_the_context(rec, monkeypatch):
    """The spec the model writes has to actually reach `Context.sandbox_seed`.

    It is declared on the response schema and rendered by `render_seed_script`, but
    between them it has to be copied out of the response — and when it isn't, every
    assistant silently falls back to the generic sales CSV, including the ones whose
    skills tell the agent to open a claims PDF.
    """

    class _FakeLLM:
        def with_structured_output(self, _schema):
            return self

        def invoke(self, _messages):
            return S.AssistantSetupResponse(
                industry="Insurance",
                actions=[],
                data_gap="claim cycle time",
                gap_action=S._QuickAction(label="A", question="Q?"),
                seed_files=[
                    S._SeedFile(
                        name="claims.csv",
                        kind="csv",
                        description="Open claims",
                        columns=["id", "amount"],
                        rows=[["1", "200"]],
                    )
                ],
            )

    monkeypatch.setattr(S, "init_chat_model", lambda *a, **k: _FakeLLM())
    analysis = S.analyze_customer("Acme Insurance")
    assert [f["name"] for f in analysis["seed_files"]] == ["claims.csv"]

    monkeypatch.setattr(S, "analyze_customer", lambda *a, **k: analysis)
    ctx = S.prepare_assistant({"workspace": "ws1", "customer": "Acme Co"})["context"]
    assert [f["name"] for f in ctx["sandbox_seed"]] == ["claims.csv"]


def test_seed_files_are_capped(rec, monkeypatch):
    """Model-written spec: capped here as well as at render time."""
    many = [{"name": f"f{i}.csv", "kind": "csv", "description": "d"} for i in range(9)]
    ctx = _prep(monkeypatch, _analysis(seed_files=many))["context"]
    assert len(ctx["sandbox_seed"]) == 9  # prepare_assistant passes through...
    # ...and analyze_customer is where the model's list gets trimmed.
    assert S._MAX_SEED_FILES == 4


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
    started: list[tuple] = []
    monkeypatch.setattr(
        S, "start_demo_traffic", lambda ws, project, **kw: started.append((ws, project, kw))
    )
    return started


def test_backfill_is_opt_in(rec, monkeypatch, traffic):
    """No `demo_traffic` in the payload means an empty project.

    It is thousands of runs in the CUSTOMER's project, priced by LangSmith as if they
    were real, which is a bad thing to find unannounced. The Settings panel can still
    generate it later, so off by default defers it rather than losing it.
    """
    _prep(monkeypatch, _analysis(), failure_mode="hallucination")
    assert traffic == []


def test_setup_starts_the_backfill_in_the_assistants_own_trace_project(rec, monkeypatch, traffic):
    """Traffic goes through start_demo_traffic, so the panel and Generate can see it.

    Spawned bare, the setup backfill is invisible to `POST /demo-traffic`: the panel
    then shows the pre-backfill empty state while it runs, and Generate starts a second
    one on top of it.
    """
    out = _prep(monkeypatch, _analysis(), failure_mode="hallucination", demo_traffic=True)
    assert len(traffic) == 1
    workspace, project, kwargs = traffic[0]
    assert workspace == "ws1"
    assert project == out["context"]["ls_project"]
    # The gap probe is what Insights clusters on, so it still has to reach the
    # backfill, even though the gap is not on the assistant's context.
    assert kwargs["data_gap"]
    assert kwargs["data_gap"] == _analysis()["data_gap"]
    assert kwargs["customer"] == "Acme Co"
    assert len(kwargs["actions"]) == 3


def test_no_push_means_no_backfill(rec, monkeypatch, traffic):
    # push_prompts=False is the dry-run setup: no prompt, no dataset, and no traffic
    # (a project nothing was pushed to is not the one the demo will use) — even when
    # the traffic was asked for.
    _prep(monkeypatch, _analysis(), push_prompts=False, demo_traffic=True)
    assert traffic == []


def test_the_graph_forwards_every_input_it_declares():
    """`_INPUT_KEYS` must cover every non-output field of `SetupState`.

    `_run` builds its payload from `_INPUT_KEYS` alone, so a field declared on the state but
    missing from that tuple is dropped SILENTLY - the switch arrives as "off" and nothing
    anywhere errors. That has now happened twice: to the voice flag on its first pass, and to
    `demo_traffic`, which never once reached `prepare_assistant` from the create form.

    Asserted as a set relationship rather than by listing keys, so the next field added to
    the state is covered without anyone remembering to extend this test.
    """
    outputs = {"result", "status", "error"}
    declared = set(SetupState.__annotations__) - outputs
    assert declared - set(_INPUT_KEYS) == set(), "declared on SetupState but never forwarded"


def test_demo_traffic_reaches_prepare_assistant(monkeypatch):
    """The one key that decides whether traffic runs. Opt-in, so both directions matter."""
    seen: dict = {}
    monkeypatch.setattr(
        setup_graph, "prepare_assistant", lambda payload: seen.update(payload) or {}
    )
    setup_graph._run({"workspace": "ws1", "customer": "Acme Co", "demo_traffic": True})
    assert seen["demo_traffic"] is True

    seen.clear()
    setup_graph._run({"workspace": "ws1", "customer": "Acme Co"})
    assert "demo_traffic" not in seen  # absent means off; prepare_assistant defaults it


# --- voice mode (universal, no flag) ---


def test_every_assistant_can_be_spoken_to(rec, monkeypatch):
    """No `enabled` flag: the mic is in every assistant's composer.

    Voice is deliberately NOT a per-assistant switch that also chooses the landing
    screen: one setting doing two unrelated jobs leaves most assistants mute for no
    reason anyone can name. Every assistant opens the same typing-first screen with a
    mic in the composer. `voice` stays as a dict because the voice NAME lives there,
    set in Settings.

    In metadata rather than context, because the agent knows nothing about voice: the
    whole feature is in the browser (frontend/src/lib/voice.ts).
    """
    out = _prep(monkeypatch, _analysis())
    assert out["metadata"]["voice"] == {}
    assert "voice" not in out["context"]


# --- a failed analysis must not become a generic assistant ---


def test_a_failed_analysis_stops_setup_instead_of_going_generic(rec, monkeypatch):
    """The McKesson case: every LLM-derived field silently falls back to its default.

    `analyze_customer` returns an all-defaults dict when its one LLM call fails, so
    swallowing that exception hands the presenter a correctly branded assistant
    (Brandfetch runs in the other thread and is unaffected) with no personas, no
    skills, no seed files, no industry and no tool selection - a demo that looks like
    it worked. The reported case showed quick actions about aid in Egypt, Iran and
    Canada, filled in from a stock fallback set in the SPA; SettingsPanel.tsx's
    DEFAULT_ACTIONS is empty for that reason, so such an assistant shows no quick
    actions at all.
    """
    failed = _analysis(actions=[], skills=[], industry="", error="APIStatusError: 529")
    with pytest.raises(RuntimeError, match="could not analyze"):
        _prep(monkeypatch, failed)


def test_the_error_names_the_cause_and_says_nothing_was_created(rec, monkeypatch):
    """A presenter reads this message on stage, so it has to say what to do."""
    failed = _analysis(actions=[], error="APIStatusError: 529 overloaded_error")
    with pytest.raises(RuntimeError) as exc:
        _prep(monkeypatch, failed)

    assert "529" in str(exc.value)
    assert "Nothing was created" in str(exc.value)


def test_an_analysis_with_no_seed_files_stops_setup(rec, monkeypatch):
    """The other half of the McKesson case: the data, not the personas.

    A partial analysis can produce quick actions and then die before the seed files,
    which clears the check above. Such an assistant is created with an empty
    /workspace/data: there is no generic dataset to plant in its place, and the retail
    `sales.csv` fallback that once stood in for one is exactly how a medical-distribution
    assistant ended up holding someone else's data. Refuse here, where nothing has been
    created yet.
    """
    with pytest.raises(S.SeedSpecError, match="no starting data files"):
        _prep(monkeypatch, _analysis(seed_files=[]))


def test_the_seed_file_refusal_names_the_partial_failure(rec, monkeypatch):
    with pytest.raises(S.SeedSpecError) as exc:
        _prep(monkeypatch, _analysis(seed_files=[], error="ValidationError: seed_files"))

    assert "seed_files" in str(exc.value) and "Nothing was created" in str(exc.value)


def test_caller_supplied_actions_survive_a_failed_analysis(rec, monkeypatch):
    """Setup is only refused when there is nothing to fall back to.

    A caller that passed its own quick actions has supplied the thing the analysis
    exists to produce, so the run continues.
    """
    failed = _analysis(actions=[], error="APIStatusError: 529")
    out = _prep(monkeypatch, failed, actions=[{"label": "Mine", "question": "Q?"}])
    # Asserted on the question, not the label: skill actions lead the list and
    # `_persona_label` prefixes a bare label with a persona.
    assert "Q?" in [a["question"] for a in out["actions"]]


def test_a_successful_analysis_carries_no_error(rec, monkeypatch):
    """The guard keys off `error`, so a success must not leave one behind."""
    out = _prep(monkeypatch, _analysis())
    assert len(out["actions"]) == 3


# --- the VM name is per assistant, not per customer ---


def test_each_assistant_gets_its_own_sandbox_key(rec, monkeypatch):
    """Two assistants for the same customer must not share a VM.

    The runtime keyed on `agent_repo or customer`, both derived from the customer
    name, so the second assistant attached to the first one's VM and skipped its own
    seed. That is how a McKesson assistant with four seed files in its context ended
    up with nothing on disk but a `sales.csv` from an earlier McKesson setup.
    """
    first = _prep(monkeypatch, _analysis())["context"]["sandbox_key"]
    second = _prep(monkeypatch, _analysis())["context"]["sandbox_key"]
    assert first != second
    # Still legible in a VM listing: the customer, then a disambiguator.
    assert first.startswith("acme-co-") and second.startswith("acme-co-")


def test_the_prewarm_is_given_the_same_key_it_will_be_asked_for(rec, monkeypatch):
    """A prewarm under a different name leaves an orphan VM and a cold first turn."""
    seen: dict = {}
    called = threading.Event()

    def _record(**kw):
        seen.update(kw)
        called.set()

    # The prewarm is fire-and-forget on its own thread, so the assertion has to wait
    # for it rather than race it.
    monkeypatch.setattr(S, "prewarm_sandbox", _record)
    ctx = _prep(monkeypatch, _analysis())["context"]
    assert called.wait(5), "prewarm was never called"
    assert seen.get("sandbox_key") == ctx["sandbox_key"]


# --- idempotent pushes vs. real failures ------------------------------------
# Every Context Hub push here is re-run on a re-setup, so "this exact content is
# already committed" has to count as success. The signal is the SDK's typed 409, and
# these pin that a real failure still travels. Don't decide it by hunting "409" or
# "conflict" in the exception's MESSAGE: any error quoting a URL or a request id
# satisfies that, so a genuinely failed push reports success and the assistant
# references a repo that holds nothing.


class _PushClient:
    """A workspace client whose pushes raise whatever the test hands it."""

    def __init__(self, error: BaseException):
        self.error = error

    def push_agent(self, repo, *, files=None, description=None):
        raise self.error

    def push_skill(self, repo, *, files=None, description=None):
        raise self.error


def _pushes_raise(monkeypatch, error: BaseException):
    monkeypatch.setattr(S, "_ws_client", lambda workspace: _PushClient(error))


def test_an_identical_re_push_counts_as_success(monkeypatch):
    """The SDK raises a 409 as LangSmithConflictError, and 409 means "already there"."""
    _pushes_raise(monkeypatch, LangSmithConflictError("Conflict for /repos/acme"))
    assert S.push_agent_prompt("ws", "acme-agent", "prompt text") == "(exists) acme-agent"
    assert S.push_skills_bundle("ws", "acme", "Acme", [_SKILL]) == "acme-skills"


def test_nothing_to_commit_counts_as_success_whatever_its_status(monkeypatch):
    """The narrow wire-format fallback: the wording, but only this exact wording."""
    _pushes_raise(monkeypatch, RuntimeError("400: Nothing to commit: content unchanged"))
    assert S.push_agent_prompt("ws", "acme-agent", "prompt text") == "(exists) acme-agent"


@pytest.mark.parametrize(
    "error",
    [
        # Each of these would satisfy a substring test by accident: a request id with
        # 409 in it, and a URL with "conflict" in the host.
        RuntimeError("500 server error, request id 7c409ab2"),
        RuntimeError("connection refused: conflict-resolver.internal"),
        PermissionError("403 Forbidden"),
    ],
)
def test_a_real_push_failure_is_not_reported_as_success(monkeypatch, error):
    """Setup must fail rather than hand back a repo handle for a repo it never wrote."""
    _pushes_raise(monkeypatch, error)
    with pytest.raises(type(error)):
        S.push_agent_prompt("ws", "acme-agent", "prompt text")

    with pytest.raises(type(error)):
        S.push_skills_bundle("ws", "acme", "Acme", [_SKILL])


def test_one_skill_that_will_not_push_is_skipped_and_reported(monkeypatch, capsys):
    """Best-effort, so it does not raise - but silence makes a missing skill undebuggable."""
    _pushes_raise(monkeypatch, RuntimeError("500 server error"))
    assert S.push_workflow_skills("ws", "acme", "Acme", [_SKILL]) == {}
    assert "returns-check" in capsys.readouterr().out
