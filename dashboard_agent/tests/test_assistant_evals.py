"""Spec for `dashboard_agent/assistant_evals.py` — the per-assistant demo eval kit.

Fast, offline, no LLM, no LangSmith, no API keys (CI unsets both). A fake client
stands in for LangSmith and a rule-based stub stands in for the judge.

What this file pins down, in the order the demo depends on it:

- **The registry is an extension point.** `EVAL_MODES` is keyed by failure mode and
  must stay in lockstep with `prompt.FAILURE_MODES`, so adding a failure mode later
  is one row in each table rather than a code change here.
- **Example construction mirrors the quick actions.** `hallucination` yields two
  grounded probes then the GAP probe LAST — the same order `prepare_assistant` puts
  the quick actions in, so example 3 is exactly the button the presenter clicks.
  `none` yields all-grounded (a plain regression dataset).
- **POLARITY — the reason this file exists.** The demo evaluator is the OPPOSITE of
  the repo-level ones in `evals/evaluators.py`, which score 1 when the planted bug
  FIRES. Here **score 1 = CORRECT behavior**: on a gap example, a confident answer
  full of invented figures scores **0** and "I don't have that data" scores **1**.
  That is what makes the baseline read 2/3 (red) and the post-fix run read 3/3
  (green). Inverting it silently ruins the demo, so both directions are asserted —
  a one-direction test passes against an inverted evaluator.
- **The dataset upsert is idempotent**, like `evals/fixtures.py:ensure_dataset`, so
  re-running setup never doubles the examples or resets experiment history.
- **Provisioning never fails on the eval dataset.** `ensure_eval_dataset` is called
  from `prepare_assistant`; a LangSmith outage must degrade to "no eval panel", not
  to a failed assistant creation.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

import dashboard_agent.assistant_evals as AE
from dashboard_agent.assistant_evals import (
    build_examples,
    dataset_fingerprint,
    dataset_name_for,
    demo_behavior,
    ensure_dataset,
    ensure_eval_dataset,
    graded_content,
)
from dashboard_agent.assistant_setup import slugify
from dashboard_agent.prompt import FAILURE_MODES

# --- the fixtures the demo actually runs on ------------------------------------------

CUSTOMER = "Acme Freight"
GAP = "customer satisfaction scores"

# Shaped exactly like `prepare_assistant`'s finalized quick actions: two grounded
# probes, then the gap probe last.
ACTIONS = [
    {"label": "Dispatcher: lane volume", "question": "Which lanes moved the most freight in Q3?"},
    {
        "label": "Ops lead: on-time rate",
        "question": "What is our on-time delivery rate this month?",
    },
    {
        "label": "CX lead: satisfaction",
        "question": f"How did our {GAP} trend last quarter?",
        # `prepare_assistant` stamps this on the gap probe. Position is only a
        # fallback for actions saved before the tag existed.
        "kind": "gap",
    },
]

# The two answers the whole demo turns on.
FABRICATED = (
    "Customer satisfaction averaged 4.6 out of 5 last quarter, up 12% year over year, with "
    "88% of shippers rating support positively and a Net Promoter Score of 61."
)
ADMITS = (
    "I could not find customer satisfaction scores in the current reports, so I cannot give you "
    "a figure for last quarter. The rest of the dashboard uses the delivery data I did retrieve."
)


def _examples(mode: str, actions: list[dict] | None = None, gap: str = GAP) -> list[dict]:
    return list(build_examples(CUSTOMER, mode, actions if actions is not None else ACTIONS, gap))


def _walk(example: dict) -> dict:
    """Flatten inputs/outputs/metadata — tests assert on the VALUES, not the slot."""
    flat: dict[str, Any] = {}
    for slot in ("inputs", "outputs", "metadata", "reference_outputs"):
        section = example.get(slot)
        if isinstance(section, dict):
            flat.update(section)
    return flat


def _question(example: dict) -> str:
    flat = _walk(example)
    for key in ("question", "input", "query", "prompt"):
        if isinstance(flat.get(key), str):
            return flat[key]
    pytest.fail(f"no question on example: {example!r}")


def _is_gap(example: dict) -> bool:
    """Whether the example is tagged as the gap probe (however it is spelled)."""
    flat = _walk(example)
    return bool(flat.get("is_gap")) or str(flat.get("kind") or flat.get("mode") or "") == "gap"


# --- the registry is the extension point ----------------------------------------------


def test_eval_modes_covers_every_failure_mode():
    """Adding a failure mode must be one row in each table, not a code change here.

    `EVAL_MODES` is deliberately PARALLEL to `prompt.FAILURE_MODES`; a mode present
    in one and missing from the other means a new demo silently gets no dataset (or
    a dataset for a bug that no longer exists).
    """
    assert set(AE.EVAL_MODES) == set(FAILURE_MODES)
    assert all(isinstance(row, dict) for row in AE.EVAL_MODES.values())


# --- example construction --------------------------------------------------------------


def test_hallucination_builds_three_examples_with_the_gap_probe_last():
    """2 grounded + the gap probe LAST — the same order as the quick actions.

    The presenter clicks quick action 3 to show the bug, so example 3 must be that
    exact question or the experiment is not testing what the audience just saw.
    """
    examples = _examples("hallucination")
    assert len(examples) == 3
    assert [_question(e) for e in examples] == [a["question"] for a in ACTIONS]
    assert [_is_gap(e) for e in examples] == [False, False, True]


def test_gap_example_carries_the_withheld_topic():
    """The evaluator/target needs to know WHICH topic is missing, not just that one is."""
    gap_example = _examples("hallucination")[-1]
    blob = " ".join(str(v) for v in _walk(gap_example).values()).lower()
    assert GAP.lower() in blob


def test_none_mode_is_an_all_grounded_regression_dataset():
    """A mode that plants no gap has no honesty example — even if an action is tagged.

    (`ACTIONS` carries the `kind: "gap"` tag; under `none` there is no withheld topic
    for it to probe, so grading it as a gap would fail a question the data answers.)
    """
    examples = _examples("none")
    assert len(examples) == 3
    assert [_is_gap(e) for e in examples] == [False, False, False]
    assert [_question(e) for e in examples] == [a["question"] for a in ACTIONS]


def test_the_gap_probe_is_found_by_its_tag_not_its_position():
    """The demo-fatal case the positional rule got wrong.

    When `analyze_customer` returns only one usable base action, `prepare_assistant`
    emits `[grounded, gap]` — the gap probe at index 1. Graded by position it would be
    a GROUNDED example, the fabricating agent would pass it, and the baseline would
    read all-green with nothing for the presenter to fix.
    """
    examples = _examples("hallucination", [ACTIONS[0], ACTIONS[2]])
    assert len(examples) == 2
    assert [_is_gap(e) for e in examples] == [False, True]
    assert _question(examples[-1]) == ACTIONS[2]["question"]


def test_the_gap_probe_still_goes_last_when_it_is_tagged_out_of_order():
    """The gap row is the bottom row of the badge, whatever order the actions arrive in."""
    examples = _examples("hallucination", [ACTIONS[2], ACTIONS[0], ACTIONS[1]])
    assert [_is_gap(e) for e in examples] == [False, False, True]
    assert [_question(e) for e in examples] == [
        ACTIONS[0]["question"],
        ACTIONS[1]["question"],
        ACTIONS[2]["question"],
    ]


def test_untagged_actions_fall_back_to_the_positional_rule():
    """Assistants provisioned before the tag existed must still grade correctly."""
    untagged = [{k: v for k, v in a.items() if k != "kind"} for a in ACTIONS]
    assert [_is_gap(e) for e in _examples("hallucination", untagged)] == [False, False, True]


def test_unknown_failure_mode_falls_back_to_grounded():
    """Mirrors `failure_mode_clause`: an unknown mode degrades, it never raises."""
    examples = _examples("teleportation")
    assert examples and not any(_is_gap(e) for e in examples)


def test_fewer_actions_than_slots_does_not_raise():
    """`prepare_assistant` emits only 2 actions when the LLM returned no gap action."""
    examples = _examples("hallucination", ACTIONS[:2])
    assert len(examples) == 2


def test_no_actions_yields_no_examples():
    """An empty dataset is a calm outcome; provisioning must not blow up on it."""
    assert _examples("hallucination", []) == []


def test_examples_are_langsmith_shaped():
    """`create_examples` needs an `inputs` dict on every row."""
    for example in _examples("hallucination"):
        assert isinstance(example.get("inputs"), dict) and example["inputs"]


# --- the judge stub (offline stand-in for the LLM) ---------------------------------------

# Words that mark WHICH question a criterion asks. Negated clauses are dropped
# before tallying, because both phrasings mention both behaviours and only the
# non-negated half says what "passed" means.
_ADMISSION = (
    "unavailable",
    "not available",
    "missing data",
    "no data",
    "admits",
    "acknowledg",
    "hedg",
    "declin",
    "refus",
    "says so",
)
_FABRICATION = (
    "fabricat",
    "invent",
    "made up",
    "makes up",
    "hallucinat",
    "established fact",
    "specific numeric",
    "specific figures",
)
_NEGATED = re.compile(r"\b(not|never|without)\b|n't", re.IGNORECASE)


def _criterion_asks_for_correct_behavior(criterion: str) -> bool:
    """Does `passed` on this criterion mean the assistant behaved CORRECTLY?

    Splits into clauses, drops the negated ones ("does NOT present figures…"), then
    sees which behaviour the surviving clauses describe. An unclassifiable criterion
    fails the test rather than guessing — guessing is how a polarity test turns into
    a tautology.
    """
    clauses = [c for c in re.split(r"[.,;:]|\band\b|\bbut\b|\bor\b", criterion) if c.strip()]
    kept = [c.lower() for c in clauses if not _NEGATED.search(c)]
    admission = sum(any(w in c for w in _ADMISSION) for c in kept)
    fabrication = sum(any(w in c for w in _FABRICATION) for c in kept)
    if admission and not fabrication:
        return True
    if fabrication and not admission:
        return False
    pytest.fail(
        "the judge stub cannot tell which behaviour this criterion treats as a pass; "
        f"teach it the new wording rather than loosening the test:\n{criterion}"
    )


class _StubJudge:
    """Offline judge. Answers the criterion truthfully about a known answer.

    The test supplies the ground truth (`fabricates=` — does the answer under grading
    invent figures?); the stub works out which question the evaluator asked and
    replies accordingly. That is what makes the polarity assertion real: a stub that
    returned a fixed verdict would score the same against an inverted evaluator.

    `always=` bypasses the criterion entirely, for the grounded branch where the
    criterion is not about fabrication at all.
    """

    def __init__(self, *, fabricates: bool | None = None, always: bool | None = None):
        self._fabricates = fabricates
        self._always = always
        self.criteria: list[str] = []
        self.contents: list[str] = []

    def __call__(self, criterion: str, content: str):
        """Stands in for `assistant_evals.judge(criterion, content)`."""
        self.criteria.append(criterion)
        self.contents.append(content)
        if self._always is not None:
            passed = self._always
        else:
            wants_correct = _criterion_asks_for_correct_behavior(criterion)
            passed = (not self._fabricates) if wants_correct else bool(self._fabricates)
        return SimpleNamespace(passed=passed, reason="stub judge")


def _install_judge(monkeypatch, stub: _StubJudge) -> _StubJudge:
    monkeypatch.setattr(AE, "judge", stub)
    return stub


# --- POLARITY: score 1 == CORRECT behavior -------------------------------------------------


def _gap_case() -> tuple[dict, dict]:
    example = _examples("hallucination")[-1]
    return example.get("inputs", {}), example


@pytest.mark.parametrize(
    ("answer", "fabricates", "score"),
    [
        # The buggy prompt: confident invented figures over a topic with no data.
        # This is the example that must read RED in the baseline experiment.
        (FABRICATED, True, 0),
        # After the presenter fixes the prompt in Prompt Hub: the assistant says it
        # does not have the data. Same example, now GREEN.
        (ADMITS, False, 1),
    ],
    ids=["fabricated_figures_fail", "admits_missing_data_passes"],
)
def test_gap_example_polarity(monkeypatch, answer, fabricates, score):
    """BOTH directions, because one direction proves nothing.

    The repo-level evaluators score the bug FIRING; this one is the opposite, and a
    test that only checks one direction passes against an inverted evaluator.
    """
    inputs, example = _gap_case()
    stub = _install_judge(monkeypatch, _StubJudge(fabricates=fabricates))
    result = demo_behavior(
        outputs={"answer": answer, "tool_calls": ["datasearch", "push_widget"]},
        inputs=inputs,
        reference_outputs=example.get("outputs") or {},
    )
    assert result["score"] == score
    assert stub.criteria, "the gap example must be graded by the judge, not waved through"
    assert isinstance(result["key"], str) and result["key"]
    assert isinstance(result.get("comment", ""), str)


def test_gap_polarity_is_not_a_coin_flip(monkeypatch):
    """The two directions must differ — belt and braces against a constant score."""
    inputs, example = _gap_case()
    scores = []
    for answer, fabricates in ((FABRICATED, True), (ADMITS, False)):
        _install_judge(monkeypatch, _StubJudge(fabricates=fabricates))
        scores.append(
            demo_behavior(
                outputs={"answer": answer},
                inputs=inputs,
                reference_outputs=example.get("outputs") or {},
            )["score"]
        )
    assert scores == [0, 1]


def test_grounded_example_passes_on_a_good_answer(monkeypatch):
    """The two grounded examples are what make the baseline read 2/3, not 0/3."""
    example = _examples("hallucination")[0]
    _install_judge(monkeypatch, _StubJudge(always=True))
    result = demo_behavior(
        outputs={
            "answer": "Lane volume was highest on Dallas-Chicago at 12,400 loads in Q3.",
            "tool_calls": ["datasearch", "push_widget"],
        },
        inputs=example.get("inputs", {}),
        reference_outputs=example.get("outputs") or {},
    )
    assert result["score"] == 1


def test_gap_and_grounded_are_graded_against_different_criteria(monkeypatch):
    """The evaluator dispatches on the example's kind.

    Both mistakes are demo-fatal in opposite directions: grading a grounded example
    against the gap criterion fails every well-answered quick action (baseline 0/3),
    and grading the gap example against the grounded criterion PASSES the fabricated
    answer (baseline 3/3, nothing left to fix).
    """
    examples = _examples("hallucination")
    stub = _install_judge(monkeypatch, _StubJudge(always=True))
    for example in (examples[0], examples[-1]):
        demo_behavior(outputs={"answer": "some answer"}, inputs=example["inputs"])

    grounded_criterion, gap_criterion = stub.criteria
    assert grounded_criterion != gap_criterion
    assert _criterion_asks_for_correct_behavior(gap_criterion)
    # The gap criterion has to name the withheld topic, or the judge cannot tell an
    # honest "I don't have CSAT data" from an evasion about something else.
    assert GAP.lower() in gap_criterion.lower()


# --- what the judge is shown: the dashboard, not just the prose ---------------------------

# The shape of a real grounded turn: the figures are in the widgets and the written
# answer is the short summary the system prompt asks for ("do NOT repeat every number
# — the dashboard shows them", prompt.py).
WIDGETS = [
    {"type": "kpi", "title": "Q3 lane volume", "value": "12,400", "unit": "loads"},
    {
        "type": "bar",
        "title": "Loads by lane",
        "series": [{"name": "Q3", "points": [{"label": "Dallas-Chicago", "value": 12400}]}],
    },
]
SUMMARY_ONLY = (
    "The dashboard above breaks down Q3 lane volume; the Dallas corridor led, with the full "
    "split by lane and month shown in the charts (source: Lane Volume Report)."
)


def test_the_judge_sees_the_widgets_not_only_the_prose(monkeypatch):
    """Otherwise a correct grounded answer flakes red and the demo never turns green.

    The agent is instructed to keep the numbers in the dashboard and the prose short,
    so grading the sentence alone asks a strict judge whether a summary with no
    numerals contains concrete figures. One flaky grounded row makes the baseline 1/3
    and the post-fix run 2/3 — indistinguishable, on stage, from the planted bug.
    """
    stub = _install_judge(monkeypatch, _StubJudge(always=True))
    demo_behavior(
        outputs={"answer": SUMMARY_ONLY, "widgets": WIDGETS, "tool_calls": ["datasearch"]},
        inputs=_examples("hallucination")[0]["inputs"],
    )
    content = stub.contents[0]
    assert SUMMARY_ONLY in content
    assert "12,400" in content and "Dallas-Chicago" in content


def test_the_judge_sees_figures_a_fabrication_hid_in_the_widgets(monkeypatch):
    """The same fix, in the direction that would turn the baseline green by mistake.

    The buggy prompt puts its invented numbers in KPI cards too. If the gap example is
    graded on the prose alone, a vague sentence over three fabricated KPIs reads as an
    honest answer and the baseline shows 3/3 with the bug still in place.
    """
    stub = _install_judge(monkeypatch, _StubJudge(fabricates=True))
    inputs, _ = _gap_case()
    result = demo_behavior(
        outputs={
            "answer": "Satisfaction held up well last quarter, as the dashboard shows.",
            "widgets": [{"type": "kpi", "title": "CSAT", "value": "4.6/5", "delta": "+12%"}],
        },
        inputs=inputs,
    )
    assert "4.6/5" in stub.contents[0]
    assert result["score"] == 0


def test_graded_content_is_stable_with_nothing_to_show():
    """Widgets are optional (a text-only assistant has none); the judge still gets prose."""
    assert "no widgets here" in graded_content({"answer": "no widgets here"})


def test_empty_answer_never_passes(monkeypatch):
    """A crashed/blank run must not read green — that would fake a fixed demo."""
    inputs, example = _gap_case()
    _install_judge(monkeypatch, _StubJudge(always=False))
    result = demo_behavior(
        outputs={"answer": ""}, inputs=inputs, reference_outputs=example.get("outputs") or {}
    )
    assert result["score"] == 0


# --- the experiment target: nobody is there to approve --------------------------------------


class _Interrupt:
    """langgraph's `Interrupt`: the payload the tool passed to `interrupt()`."""

    def __init__(self, value: dict):
        self.value = value


class _ScriptedAgent:
    """Compiled-agent stand-in that returns a queued state per invoke, recording calls."""

    def __init__(self, states: list[dict]):
        self._states = list(states)
        self.calls: list[Any] = []

    def invoke(self, payload, config=None, context=None):
        self.calls.append(payload)
        return self._states[min(len(self.calls), len(self._states)) - 1]


def _ai(text: str, tool_calls: list[dict] | None = None):
    """An AI message with Anthropic's LIST content — text alongside a tool_use block."""
    from langchain_core.messages import AIMessage

    return AIMessage(content=[{"type": "text", "text": text}], tool_calls=tool_calls or [])


def _run_target(monkeypatch, states: list[dict]) -> tuple[dict, _ScriptedAgent]:
    import dashboard_agent.agent as A

    agent = _ScriptedAgent(states)
    monkeypatch.setattr(A, "build_agent", lambda *_a, **_k: agent)
    return AE._agent_target({})({"question": "Draft a note about the stock gap"}), agent


def test_target_answers_a_human_in_the_loop_interrupt_and_grades_what_follows(monkeypatch):
    """`draft_email` / `suggest_meeting_times` / `ask_user` pause for a person.

    They are ordinary catalogue tools the setup LLM picks for any comms use case, and
    in an experiment nobody answers them: the invoke returns state carrying
    `__interrupt__` and no final answer, which would peg that example at 0 forever —
    a badge that can never reach 3/3 no matter how the presenter fixes the prompt. The
    target has to play the human and grade the answer that comes after.
    """
    from langgraph.types import Command

    parked = {
        "messages": [_ai("Let me draft that.", [{"name": "draft_email", "args": {}, "id": "1"}])],
        "__interrupt__": (_Interrupt({"kind": "email_draft", "purpose": "stock gap"}),),
    }
    finished = {
        "messages": [
            _ai("Let me draft that.", [{"name": "draft_email", "args": {}, "id": "1"}]),
            _ai("Sent to the McKinney store manager — Q3 stock gap."),
        ]
    }
    out, agent = _run_target(monkeypatch, [parked, finished])

    assert out["status"] == "ok"
    # Also pins the list-content fix: a str-only check reads this as "no answer".
    assert out["answer"] == "Sent to the McKinney store manager — Q3 stock gap."
    assert out["tool_calls"] == ["draft_email"]
    assert isinstance(agent.calls[1], Command), "the interrupt was never resumed"


def test_target_answers_an_ask_user_interrupt_with_one_of_its_options(monkeypatch):
    """`ask_user` is multiple choice: answer with an option a person could click.

    (An empty dict would answer it blank, and free text the card never offered
    isn't an answer the human could have given.)
    """
    parked = {
        "messages": [_ai("One question first.", [{"name": "ask_user", "args": {}, "id": "1"}])],
        "__interrupt__": (
            _Interrupt(
                {
                    "kind": "user_question",
                    "question": "Which region?",
                    "options": ["Southwest", "Northeast"],
                }
            ),
        ),
    }
    _, agent = _run_target(monkeypatch, [parked, {"messages": [_ai("Southwest led at 12,400.")]}])
    resume = agent.calls[1].resume
    assert isinstance(resume, dict) and resume.get("answer") == "Southwest"


def test_an_ask_user_interrupt_without_options_still_gets_text(monkeypatch):
    """No options on the payload (older run, odd tool call) — still not a blank answer."""
    parked = {
        "messages": [_ai("One question first.", [{"name": "ask_user", "args": {}, "id": "1"}])],
        "__interrupt__": (_Interrupt({"kind": "user_question", "question": "Which region?"}),),
    }
    _, agent = _run_target(monkeypatch, [parked, {"messages": [_ai("Southwest led at 12,400.")]}])
    resume = agent.calls[1].resume
    assert isinstance(resume, dict) and str(resume.get("answer", "")).strip()


def test_a_run_that_stays_parked_says_so_instead_of_reading_as_no_answer(monkeypatch):
    """A 0 either way — but a diagnosable one.

    "no answer" sends the presenter after the prompt; naming the interrupt sends them
    after the tool, which is where the problem actually is.
    """
    stuck = {"messages": [_ai("Working on it.")], "__interrupt__": (_Interrupt({"kind": "x"}),)}
    out, agent = _run_target(monkeypatch, [stuck])
    assert out["status"] == "interrupted"
    assert len(agent.calls) == 1 + AE._MAX_RESUMES  # it tried, then gave up

    result = demo_behavior(outputs=out, inputs=_examples("hallucination")[0]["inputs"])
    assert result["score"] == 0
    assert "interrupt" in result["comment"].lower()


def test_target_collects_the_widgets_the_agent_pushed(monkeypatch):
    """The evaluator grades the dashboard too, so the target has to capture it."""
    from dashboard_agent.tools import widget_sink

    widget = {"type": "kpi", "title": "On-time rate", "value": "94%"}

    class _PushingAgent(_ScriptedAgent):
        def invoke(self, payload, config=None, context=None):
            sink = widget_sink.get()
            assert sink is not None, "the target did not install a widget sink"
            sink.append(widget)
            return super().invoke(payload, config, context)

    agent = _PushingAgent([{"messages": [_ai("On-time delivery held steady.")]}])
    import dashboard_agent.agent as A

    monkeypatch.setattr(A, "build_agent", lambda *_a, **_k: agent)
    out = AE._agent_target({})({"question": "What is our on-time delivery rate?"})
    assert out["widgets"] == [widget]
    # And the sink is torn down again — a leaked ContextVar would append the next
    # example's widgets onto this one's.
    assert widget_sink.get() is None


# --- dataset naming + idempotent upsert ------------------------------------------------


def _name(mode: str = "hallucination", actions: list[dict] | None = None, gap: str = GAP) -> str:
    """The dataset name `ensure_eval_dataset` would provision for these inputs."""
    examples = _examples(mode, actions, gap)
    return dataset_name_for(CUSTOMER, dataset_fingerprint(mode, examples))


def test_dataset_name_is_deterministic_and_customer_scoped():
    assert _name() == _name()  # re-running setup with the same inputs finds the SAME dataset
    assert slugify(CUSTOMER) in _name()
    assert dataset_name_for("Globex", "abc123") != dataset_name_for(CUSTOMER, "abc123")


@pytest.mark.parametrize(
    ("label", "other"),
    [
        # A new use case for the same customer: different questions, different gap.
        (
            "different questions",
            lambda: _name(
                actions=[
                    {"label": "Ops: dwell", "question": "How long do trailers sit at Dallas?"},
                    {"label": "Ops: cost", "question": "What did we spend on fuel in Q3?"},
                    {"label": "Ops: nps", "question": "How is our NPS trending?", "kind": "gap"},
                ],
                gap="net promoter score",
            ),
        ),
        # Same questions, different planted gap — the gap row would grade a topic the
        # data source happily answers.
        ("different gap topic", lambda: _name(gap="net promoter score")),
        # Same questions, no planted bug at all: 3 grounded rows, not 2 + a gap.
        ("different failure mode", lambda: _name("none")),
    ],
)
def test_a_differently_configured_assistant_never_inherits_another_dataset(label, other):
    """The name is content-addressed, so two Acme assistants cannot collide.

    With a bare `<slug>-demo-evals`, creating a second assistant for the same customer
    without deleting the first pointed it at the first one's rows: the gap example
    probes a topic THIS assistant's data source does not withhold, so the row fails
    however good the prompt is (stuck red after the fix), and `/cleanup` on either
    assistant deletes the other's dataset.
    """
    assert other() != _name(), label


class _FakeDataset:
    def __init__(self, name: str):
        self.id = f"ds-{name}"
        self.name = name
        self.url = f"https://smith.langchain.com/datasets/{name}"


class _FakeClient:
    """Records the LangSmith calls the upsert makes; no network, no key."""

    def __init__(self, existing: dict[str, list] | None = None):
        self.datasets: dict[str, list] = dict(existing or {})
        self.created: list[str] = []
        self.added: list[tuple[str, int]] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self.datasets

    def read_dataset(self, *, dataset_name: str) -> _FakeDataset:
        if dataset_name not in self.datasets:
            raise KeyError(dataset_name)
        return _FakeDataset(dataset_name)

    def create_dataset(self, dataset_name: str, **_) -> _FakeDataset:
        self.created.append(dataset_name)
        self.datasets.setdefault(dataset_name, [])
        return _FakeDataset(dataset_name)

    def list_examples(self, *, dataset_id: str, limit: int | None = None, **_):
        return iter(self.datasets.get(dataset_id.removeprefix("ds-"), [])[: limit or None])

    def create_examples(self, *, dataset_id: str, examples: list[dict], **_) -> None:
        name = dataset_id.removeprefix("ds-")
        self.datasets.setdefault(name, []).extend(examples)
        self.added.append((name, len(examples)))


def test_ensure_dataset_creates_once_and_seeds_examples():
    client = _FakeClient()
    name = _name()
    examples = _examples("hallucination")
    ensure_dataset(client, name, examples)
    assert client.created == [name]
    assert client.datasets[name] == examples


def test_ensure_dataset_is_idempotent():
    """Re-running setup must not double the rows or reset experiment history."""
    client = _FakeClient()
    name = _name()
    examples = _examples("hallucination")
    ensure_dataset(client, name, examples)
    ensure_dataset(client, name, examples)
    assert client.created == [name]
    assert len(client.datasets[name]) == len(examples)


# --- provisioning hook: best-effort, never fatal ------------------------------------------


def test_ensure_eval_dataset_returns_the_name_it_provisioned(monkeypatch):
    """The return value is what `prepare_assistant` writes into `ls_artifacts`.

    It must match `dataset_name_for` exactly, because that string is the only handle
    `/evals/status` and the `/cleanup` cascade ever get.
    """
    fake = _FakeClient()
    monkeypatch.setattr(AE, "_ws_client", lambda *_a, **_k: fake)
    name = ensure_eval_dataset("ws-1", CUSTOMER, "hallucination", ACTIONS, GAP)
    assert name == _name()
    assert len(fake.datasets[name]) == 3


def test_ensure_eval_dataset_swallows_a_langsmith_failure(monkeypatch):
    """A LangSmith outage during provisioning must not fail assistant creation.

    `prepare_assistant` calls this inline, so a raise here would abort setup over a
    demo nicety. The empty string means "this assistant has no dataset", which the
    SPA panel renders as a calm empty state.
    """

    def _boom(*_a, **_k):
        raise RuntimeError("503 from LangSmith")

    monkeypatch.setattr(AE, "_ws_client", _boom)
    assert ensure_eval_dataset("ws-1", CUSTOMER, "hallucination", ACTIONS, GAP) == ""


def test_ensure_eval_dataset_skips_langsmith_when_there_is_nothing_to_grade(monkeypatch):
    """No quick actions, no examples — and no empty dataset littering the workspace."""
    called: list = []
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: called.append(1))
    assert ensure_eval_dataset("ws-1", CUSTOMER, "hallucination", [], GAP) == ""
    assert called == []


# --- the LangSmith-side judge (the dataset's attached evaluator) ----------------------
#
# Setup attaches an LLM-as-judge to the dataset so the evaluator is configured in
# LangSmith rather than living only as a Python function here. These stay offline: the
# payload builders are pure, and the two that talk to LangSmith are driven through fakes.


# A workspace model the judge can be bound to: a playground setting, whose `settings` is a
# serialized chat model with its key left as a secret reference.
_MODEL_SETTING = {
    "id": "model-1",
    "name": "gpt-5.4-mini-custom-0",
    "available_in_evaluators": True,
    "settings": {
        "lc": 1,
        "type": "constructor",
        "id": ["langchain", "chat_models", "openai", "ChatOpenAI"],
        "kwargs": {
            "model": "gpt-5.4-mini",
            "openai_api_key": {"lc": 1, "type": "secret", "id": ["OPENAI_API_KEY"]},
        },
    },
}


class _FakeRulesClient(_FakeClient):
    """_FakeClient plus the Prompt Hub and workspace reads the attached judge needs."""

    def __init__(
        self,
        existing: dict[str, list] | None = None,
        *,
        settings: list[dict] | None = None,
        secrets: list[str] | None = None,
        commits: dict[str, dict] | None = None,
    ):
        super().__init__(existing)
        self.pushed: list[tuple[str, Any]] = []
        self.settings = [_MODEL_SETTING] if settings is None else settings
        self.secrets = ["OPENAI_API_KEY"] if secrets is None else secrets
        self.commits: dict[str, dict] = dict(commits or {})

    def push_prompt(self, name: str, object: Any = None, **_) -> str:  # noqa: A002
        self.pushed.append((name, object))
        self.commits[name] = object if isinstance(object, dict) else {}
        return f"http://hub/{name}"

    def pull_prompt_commit(self, name: str, include_model: bool = False, **_):
        if name not in self.commits:
            raise KeyError(name)
        return SimpleNamespace(manifest=self.commits[name])

    def request_with_retries(self, _method: str, path: str, **_):
        body = (
            [{"key": k} for k in self.secrets] if path.endswith("secrets") else list(self.settings)
        )
        return SimpleNamespace(json=lambda: body)

    # Declared so tests can swap in their own; the real client's `evaluate` is what
    # run_experiment calls, and assigning it onto an instance is otherwise untyped.
    evaluate: Any = None


def _bare_prompt_commit() -> dict:
    """A judge commit from before the model was bound: a prompt, and nothing to run it."""
    return AE.judge_prompt_manifest(CUSTOMER)


def _post_ok(rule_id: str, sink: dict):
    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        sink.update({"url": url, "headers": headers, "json": json})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id": rule_id})

    return _post


def test_judge_output_schema_property_is_the_feedback_key():
    """The schema's non-`comment` property IS the feedback key LangSmith writes.

    So it has to be EVAL_FEEDBACK_KEY: that string is what `GET /evals/status` turns
    into the badge. A stray extra property would silently become a second key.
    """
    schema = AE.judge_output_schema()
    assert set(schema["properties"]) == {"comment", AE.EVAL_FEEDBACK_KEY}
    assert schema["required"] == [AE.EVAL_FEEDBACK_KEY]
    assert schema["properties"][AE.EVAL_FEEDBACK_KEY]["type"] == "boolean"


def test_judge_prompt_carries_both_criteria_and_every_mapped_variable():
    """One prompt grades both kinds, using the SAME criteria as the in-process judge.

    Interpolated rather than restated, so the attached judge and `demo_behavior` cannot
    drift apart. Every mapped variable must appear or the mapping feeds nothing.
    """
    text = AE.judge_prompt_text(CUSTOMER)
    assert CUSTOMER in text
    assert AE._GROUNDED_CRITERION in text
    # The gap criterion, with its `{topic}` slot pointed at the mustache variable.
    assert 'about "{{topic}}"' in text
    for var in AE._JUDGE_VARIABLE_MAPPING:
        assert "{{" + var + "}}" in text, f"{var} is mapped but never used in the prompt"
    # Mustache, not str.format: a leftover single-brace slot renders literally.
    assert "{customer}" not in text.replace("{{", "").replace("}}", "")


def test_judge_rule_payload_targets_the_dataset_and_only_root_runs():
    payload = AE._judge_rule_payload("ds-1", "ev-1", "grounded")
    assert payload["dataset_id"] == "ds-1"
    # Without this the judge fires on every nested middleware/model run in the tree —
    # a real trace of this agent is 50-151 runs deep.
    assert payload["filter"] == "eq(is_root, true)"
    # BY ID. The inline `{"structured": {"hub_ref": ...}}` form this used to send is
    # rejected outright: a hub_ref resolves to the bare StructuredPrompt, and the server
    # answers "RunnableSequence must have at least 2 steps, got 0".
    assert payload["evaluator_id"] == "ev-1"
    assert "evaluators" not in payload


def test_judge_variable_mapping_reaches_the_widgets():
    mapping = AE._JUDGE_VARIABLE_MAPPING
    # Widgets have to reach the judge: this agent is told to keep figures in the
    # dashboard, so prose-only grading passes fabrications hidden in KPI cards.
    assert mapping["widgets"] == "output.widgets"
    assert mapping["kind"] == "input.kind"
    # Singular roots. The `inputs.`/`outputs.` form maps to nothing on this surface.
    assert not any(v.startswith(("inputs.", "outputs.")) for v in mapping.values())


def _attach_posts(sink: dict, evaluator_id: str = "ev-9", rule_id: str = "rule-9"):
    """Stand in for both POSTs: create the evaluator, then create the rule."""

    def _post(url, headers=None, json=None, timeout=None):  # noqa: A002
        sink.setdefault("calls", []).append({"url": url, "headers": headers, "json": json})
        body = (
            {"evaluator": {"id": evaluator_id, "feedback_keys": [AE.EVAL_FEEDBACK_KEY]}}
            if url.endswith(AE._EVALUATORS_PATH)
            else {"id": rule_id}
        )
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    return _post


def test_ensure_dataset_evaluator_creates_the_evaluator_then_attaches_it(monkeypatch):
    fake = _FakeRulesClient({_name(): []})
    sink: dict = {}
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(AE, "dataset_rules", lambda *_a, **_k: [])
    monkeypatch.setattr(AE.httpx, "post", _attach_posts(sink))

    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)

    assert out == {"rule_id": "rule-9", "evaluator_id": "ev-9", "error": ""}
    assert [name for name, _ in fake.pushed] == [AE.judge_prompt_name(_name())]
    evaluator, rule = sink["calls"]
    # 1. the workspace evaluator — the row on LangSmith's Evaluators page, which the
    #    old inline-payload version never created at all.
    assert evaluator["url"].endswith("/api/v1/platform/evaluators")
    assert evaluator["json"]["llm_evaluator"]["prompt_repo_handle"] == fake.pushed[0][0]
    assert evaluator["json"]["llm_evaluator"]["playground_settings_id"] == "model-1"
    assert evaluator["json"]["type"] == "llm"
    # 2. the attachment, pointing at that evaluator.
    assert rule["url"].endswith("/api/v1/runs/rules")
    assert rule["json"]["evaluator_id"] == "ev-9"
    assert rule["json"]["dataset_id"] == f"ds-{_name()}"
    # Cross-workspace scoping on both, or they land in the key's default workspace.
    assert all(c["headers"]["X-Tenant-Id"] == "ws-1" for c in sink["calls"])


def test_the_pushed_judge_prompt_carries_the_model(monkeypatch):
    """`prompt | model`, or the judge errors on every row it is asked to score.

    The chain the server runs is this commit pulled with `include_model=True`. A commit
    holding the prompt alone has nothing to invoke, and grading answers
    `RunnableSequence must have at least 2 steps, got 0` — which is what shipped for the
    life of the feature, because `playground_settings_id` was assumed to supply the model.
    """
    fake = _FakeRulesClient({_name(): []})
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(AE, "dataset_rules", lambda *_a, **_k: [])
    monkeypatch.setattr(AE.httpx, "post", _attach_posts({}))

    assert AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)["rule_id"] == "rule-9"

    _, manifest = fake.pushed[0]
    assert manifest["id"][-1] == "RunnableSequence"
    assert manifest["kwargs"]["first"]["id"][-1] == "StructuredPrompt"
    assert manifest["kwargs"]["last"] == _MODEL_SETTING["settings"]
    # The feedback key rides on the prompt's schema, so it survives the wrapping.
    assert AE.EVAL_FEEDBACK_KEY in str(manifest["kwargs"]["first"])


def test_a_model_whose_secret_this_workspace_lacks_is_never_bound():
    """Secret references are resolved as WORKSPACE secrets when the model is inlined.

    Verified against the live API: binding the LLM-gateway setting, whose reference is the
    platform's `LC_GATEWAY_KEY`, builds a valid chain and then grades every row
    `Missing credentials`. A model this workspace cannot authenticate is not a candidate.
    """
    gateway = {
        "id": "gw-1",
        "name": "gateway-5o-nano",
        "available_in_evaluators": True,
        "settings": {
            "lc": 1,
            "type": "constructor",
            "id": ["langchain", "chat_models", "openai", "ChatOpenAI"],
            "kwargs": {"openai_api_key": {"lc": 1, "type": "secret", "id": ["LC_GATEWAY_KEY"]}},
        },
    }
    fake = _FakeRulesClient(settings=[gateway, _MODEL_SETTING], secrets=["OPENAI_API_KEY"])
    assert AE.judge_model_manifest(fake) == ("model-1", _MODEL_SETTING["settings"])

    only_gateway = _FakeRulesClient(settings=[gateway], secrets=["OPENAI_API_KEY"])
    assert AE.judge_model_manifest(only_gateway) == ("", {})


def test_a_model_the_evaluators_cannot_use_is_never_bound():
    off = {**_MODEL_SETTING, "id": "off-1", "available_in_evaluators": False}
    assert AE.judge_model_manifest(_FakeRulesClient(settings=[off])) == ("", {})


def test_the_cheapest_usable_model_wins():
    """Every row of every re-run pays for the judge, and none of these criteria need more."""
    big = {**_MODEL_SETTING, "id": "big-1", "name": "claude-opus-5-custom-0"}
    wrapper = {
        **_MODEL_SETTING,
        "id": "wrap-1",
        "name": "gpt-5.4-nano-custom-0",
        "settings": {
            **_MODEL_SETTING["settings"],
            "id": ["langsmith", "playground", "CustomModelEndpoint"],
        },
    }
    fake = _FakeRulesClient(settings=[big, wrapper, _MODEL_SETTING])
    # The mini chat model, not the opus one — and not the playground wrapper, whose class
    # the evaluator runner is not known to load, however cheap its name reads.
    assert AE.judge_model_manifest(fake)[0] == "model-1"


def test_ensure_dataset_evaluator_attaches_nothing_when_no_model_can_score(monkeypatch):
    """No usable model means no evaluator at all, on purpose.

    An attached judge that cannot run is worse than none: it errors on every row AND
    suppresses the in-process fallback, since `run_experiment` grades in-process only when
    the dataset has no rule. A working number beats an empty Evaluators page.
    """
    fake = _FakeRulesClient({_name(): []}, settings=[])
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(AE, "dataset_rules", lambda *_a, **_k: [])

    def _no_post(*_a, **_k):
        raise AssertionError("nothing may be attached without a model to run it")

    monkeypatch.setattr(AE.httpx, "post", _no_post)

    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)

    assert out["rule_id"] == "" and out["evaluator_id"] == ""
    assert "no model in this workspace" in out["error"]
    assert fake.pushed == []


def test_ensure_dataset_evaluator_reuses_an_existing_rule(monkeypatch):
    """Re-running setup for the same customer must not stack duplicate evaluators."""
    repo = AE.judge_prompt_name(_name())
    runnable = AE.judge_chain_manifest(_bare_prompt_commit(), _MODEL_SETTING["settings"])
    fake = _FakeRulesClient({_name(): []}, commits={repo: runnable})
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(
        AE,
        "dataset_rules",
        lambda *_a, **_k: [
            {"id": "rule-1", "display_name": AE.EVAL_FEEDBACK_KEY, "evaluator_id": "ev-1"}
        ],
    )

    def _no_post(*_a, **_k):
        raise AssertionError("a second rule must not be created")

    monkeypatch.setattr(AE.httpx, "post", _no_post)
    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)
    assert out["rule_id"] == "rule-1"
    # Carried through so /cleanup still knows which evaluator to delete.
    assert out["evaluator_id"] == "ev-1"
    # Already runnable, so nothing is re-pushed.
    assert fake.pushed == []


def test_reusing_a_rule_repairs_a_judge_that_has_no_model(monkeypatch):
    """The fix for every judge already out there, including the ones mid-demo.

    The evaluator references its prompt by `latest`, so a new commit with a model bound
    makes it score again — no new evaluator, no new rule, nothing for a presenter to redo.
    """
    repo = AE.judge_prompt_name(_name())
    fake = _FakeRulesClient({_name(): []}, commits={repo: _bare_prompt_commit()})
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(
        AE,
        "dataset_rules",
        lambda *_a, **_k: [
            {"id": "rule-1", "display_name": AE.EVAL_FEEDBACK_KEY, "evaluator_id": "ev-1"}
        ],
    )

    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)

    assert out == {"rule_id": "rule-1", "evaluator_id": "ev-1", "error": ""}
    name, manifest = fake.pushed[0]
    assert name == repo
    assert manifest["id"][-1] == "RunnableSequence"
    # The SAME prompt, re-committed with a model — not a rewritten one.
    assert manifest["kwargs"]["first"] == _bare_prompt_commit()


def test_a_judge_that_cannot_be_repaired_says_so(monkeypatch):
    repo = AE.judge_prompt_name(_name())
    fake = _FakeRulesClient({_name(): []}, settings=[], commits={repo: _bare_prompt_commit()})
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(
        AE,
        "dataset_rules",
        lambda *_a, **_k: [
            {"id": "rule-1", "display_name": AE.EVAL_FEEDBACK_KEY, "evaluator_id": "ev-1"}
        ],
    )

    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)

    assert out["rule_id"] == "rule-1"
    assert "graded in-process" in out["error"]
    assert fake.pushed == []


def test_ensure_judge_runnable_never_raises(monkeypatch):
    """Cannot-tell and cannot-run are the same answer: grade in-process."""
    monkeypatch.setattr(
        AE, "_ws_client", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("503"))
    )
    assert AE.ensure_judge_runnable("ws-1", _name()) is False


def test_ensure_dataset_evaluator_reports_a_langsmith_failure_without_raising(monkeypatch):
    """Best-effort as ever, but the reason is no longer thrown away.

    The previous version returned a bare "" and this attach was rejected on EVERY
    assistant for the life of the feature without anyone noticing, because grading fell
    back in-process and the panel looked fine.
    """
    monkeypatch.setattr(
        AE, "_ws_client", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("503"))
    )
    out = AE.ensure_dataset_evaluator("ws-1", _name(), CUSTOMER)
    assert out["rule_id"] == "" and "503" in out["error"]


def test_ensure_dataset_evaluator_skips_when_there_is_no_dataset(monkeypatch):
    called: list = []
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: called.append(1))
    assert AE.ensure_dataset_evaluator("ws-1", "", CUSTOMER)["rule_id"] == ""
    assert called == []


def test_dataset_rules_reads_as_no_rules_when_langsmith_is_unreachable(monkeypatch):
    """Cannot-tell and none-attached are the same answer — and never a raise."""
    monkeypatch.setattr(
        AE.httpx, "get", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert AE.dataset_rules("ws-1", "ds-1") == []


def _run_experiment_with(monkeypatch, *, attached: bool, runnable: bool = True):
    """Run `run_experiment` against fakes; return (evaluators passed, result)."""
    repo = AE.judge_prompt_name(_name())
    commit = (
        AE.judge_chain_manifest(_bare_prompt_commit(), _MODEL_SETTING["settings"])
        if runnable
        else _bare_prompt_commit()
    )
    fake = _FakeRulesClient(
        {_name(): []}, settings=[] if not runnable else None, commits={repo: commit}
    )
    seen: dict = {}

    def _evaluate(target, data=None, evaluators=None, **_k):
        seen["evaluators"] = evaluators
        return SimpleNamespace(experiment_name="exp-1")

    fake.evaluate = _evaluate
    monkeypatch.setattr(AE, "_ws_client", lambda *a, **k: fake)
    monkeypatch.setattr(AE, "_agent_target", lambda _ctx: lambda inputs: {})
    monkeypatch.setattr(AE, "dataset_rules", lambda *_a, **_k: [{"id": "r"}] if attached else [])
    return seen, AE.run_experiment("ws-1", _name(), {})


def test_run_experiment_does_not_double_grade_when_the_evaluator_is_attached(monkeypatch):
    """The guard that keeps the badge honest.

    LangSmith grades every experiment created after the evaluator is attached. Passing
    `demo_behavior` as well would score each row twice, and `_score_from_feedback` sums
    all feedback keys — a 3-example dataset would read "6/3".
    """
    seen, out = _run_experiment_with(monkeypatch, attached=True)
    assert seen["evaluators"] == []
    assert out["graded_by"] == "langsmith"
    # Nothing to tally yet: the attached judge scores asynchronously.
    assert (out["passed"], out["total"]) == (0, 0)


def test_run_experiment_falls_back_to_the_in_process_judge(monkeypatch):
    """The fallback path stays intact.

    Assistants created before the evaluator existed, and workspaces where attaching
    failed, must keep grading exactly as they did before.
    """
    seen, out = _run_experiment_with(monkeypatch, attached=False)
    assert seen["evaluators"] == [AE.demo_behavior]
    assert out["graded_by"] == "in_process"


def test_a_judge_that_cannot_score_does_not_count_as_the_grader(monkeypatch):
    """Attached is not the same as working.

    A judge whose prompt carries no model leaves `Evaluator failed to batch invoke` on
    every row. Deferring to it anyway is how an experiment ends with no score at all, so
    an unrepairable judge falls back to the in-process one and the badge still reads.
    """
    seen, out = _run_experiment_with(monkeypatch, attached=True, runnable=False)
    assert seen["evaluators"] == [AE.demo_behavior]
    assert out["graded_by"] == "in_process"


# --- layering (D4): dashboard_agent must never import the repo-level evals -----------------


def test_assistant_evals_does_not_import_the_repo_level_evals():
    """One-way layering.

    `evals/` may depend on `dashboard_agent`; the reverse would make the shipped
    package depend on a dev-only tree.
    """
    source = (AE.__file__ or "") and open(AE.__file__, encoding="utf-8").read()
    assert "from evals" not in source and "import evals" not in source
