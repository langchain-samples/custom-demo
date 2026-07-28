"""End-to-end tests that exercise the real deep agent (LLM calls).

These are slow and cost tokens. Run explicitly:

    pytest dashboard_agent/tests/test_agent_e2e.py -v

Skipped automatically if ANTHROPIC_API_KEY is unavailable.
"""

import os

import pytest

from dashboard_agent.agent import build_agent, run
from dashboard_agent.config import load_env
from dashboard_agent.widgets import validate_widget

load_env()

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

PERSONA_QUESTIONS = {
    "egypt": "What is the impact of humanitarian aid in Egypt over the last quarter, according to the latest reports?",
    "iran": "What are the available resources for displaced families in Iran as outlined in the latest situation report?",
    "canada": "Can you provide the latest data on water scarcity and sanitation needs in Canada from relevant assessments?",
}

EXPECTED_REGION = {"egypt": "egypt", "iran": "iran", "canada": "canada"}


@pytest.fixture(scope="module")
def agent():
    return build_agent()


@pytest.fixture(scope="module")
def results(agent):
    return {
        key: run(q, thread_id=f"test-{key}", agent=agent) for key, q in PERSONA_QUESTIONS.items()
    }


@pytest.mark.parametrize("persona", list(PERSONA_QUESTIONS))
def test_answer_is_grounded(results, persona):
    out = results[persona]
    assert out["answer"], "agent produced no textual answer"
    assert EXPECTED_REGION[persona] in out["answer"].lower(), "answer should mention the region"


@pytest.mark.parametrize("persona", list(PERSONA_QUESTIONS))
def test_dashboard_widgets_are_built(results, persona):
    widgets = results[persona]["widgets"]
    assert len(widgets) >= 3, f"expected a multi-widget dashboard, got {len(widgets)}"
    types = {w["type"] for w in widgets}
    assert "kpi" in types, "dashboard should include KPI cards"
    assert types & {"bar", "line", "pie"}, "dashboard should include at least one chart"


@pytest.mark.parametrize("persona", list(PERSONA_QUESTIONS))
def test_all_emitted_widgets_validate(results, persona):
    for w in results[persona]["widgets"]:
        # Round-trip through the validator; raises if the agent emitted junk.
        validate_widget(w)


def test_chart_values_are_numeric(results):
    for persona, out in results.items():
        for w in out["widgets"]:
            if w["type"] in ("bar", "line", "pie"):
                for series in w["series"]:
                    for pt in series["points"]:
                        assert isinstance(pt["value"], (int, float)), (
                            f"{persona}: non-numeric chart value {pt!r}"
                        )
