"""The software-factory demo's skills must agree with each other.

These skills are the product, and their consistency is not checkable by reading one of
them: the router's profile table references stage numbers defined in its own roster, the
phase skills claim to run particular stages, and the diagram skill draws node ids derived
from those numbers. A typo in any of those is invisible until it derails a live demo,
which is the worst possible place to find it.

So this parses the skill markdown and pins the relationships. It deliberately does NOT
check prose: the wording is meant to change without a test failing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

SKILLS = Path(__file__).resolve().parents[2] / "demos" / "sdlc-factory" / "skills"
ROUTER = SKILLS / "sdlc-workflow" / "SKILL.md"

# Stage numbers look like 0.1, 1.3, 2.7 and nothing else in this demo.
STAGE = re.compile(r"\b(\d\.\d)\b")


def _skill_files() -> list[Path]:
    return sorted(SKILLS.glob("*/SKILL.md"))


def _frontmatter(path: Path) -> dict:
    """The YAML block a skills loader reads, or a failure naming the skill."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        pytest.fail(f"{path.parent.name}: no frontmatter block")

    raw = text.split("---\n", 2)[1]
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.parent.name}: frontmatter is not valid YAML: {exc}")

    assert isinstance(loaded, dict), f"{path.parent.name}: frontmatter is not a mapping"
    return loaded


def _table_rows(text: str, header_contains: str) -> list[list[str]]:
    """Cells of the markdown table whose header row contains `header_contains`."""
    rows: list[list[str]] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if collecting:
                break
            continue

        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not collecting:
            if header_contains in stripped:
                collecting = True
            continue

        if set("".join(cells)) <= set("-: "):
            continue

        rows.append(cells)

    return rows


def _roster() -> list[str]:
    """Stage numbers the router's roster table defines, in order."""
    rows = _table_rows(ROUTER.read_text(encoding="utf-8"), "| Stage |")
    out = []
    for cells in rows:
        found = STAGE.findall(cells[0])
        if found:
            out.append(found[0])

    return out


def _profiles() -> dict[str, list[str]]:
    """Profile name to the stage numbers it declares."""
    rows = _table_rows(ROUTER.read_text(encoding="utf-8"), "| Profile |")
    out = {}
    for cells in rows:
        name = cells[0].strip("`")
        if name:
            out[name] = STAGE.findall(cells[1])

    return out


# --- every skill is loadable ---


def test_every_skill_has_frontmatter_a_loader_can_read():
    files = _skill_files()
    assert len(files) >= 7, f"expected the demo's skills, found {len(files)}"
    for path in files:
        meta = _frontmatter(path)
        # The name must match the mount directory: SkillsMiddleware keys on the
        # directory and a mismatch makes the skill unfindable by the name it advertises.
        assert meta.get("name") == path.parent.name, f"{path.parent.name}: name mismatch"
        assert str(meta.get("description") or "").strip(), f"{path.parent.name}: no description"


def test_descriptions_say_when_to_use_the_skill():
    """A description is the only thing the model sees before choosing. It has to trigger."""
    for path in _skill_files():
        description = str(_frontmatter(path)["description"]).lower()
        assert "use " in description, f"{path.parent.name}: description names no trigger"


# --- the roster and the profiles agree ---


def test_the_roster_is_twelve_ordered_stages():
    roster = _roster()
    assert len(roster) == 12, f"roster has {len(roster)} stages: {roster}"
    assert roster == sorted(roster), f"roster is out of order: {roster}"
    assert len(set(roster)) == len(roster), f"roster repeats a stage: {roster}"


def test_every_profile_runs_only_stages_the_roster_defines():
    roster = set(_roster())
    profiles = _profiles()
    assert profiles, "the router declares no profiles"
    for name, stages in profiles.items():
        assert stages, f"profile {name} runs no stages"
        unknown = [s for s in stages if s not in roster]
        assert not unknown, f"profile {name} runs stages not in the roster: {unknown}"
        assert stages == sorted(stages), f"profile {name} lists stages out of order: {stages}"


def test_the_default_profile_runs_the_whole_roster():
    """`feature` is the default, so it must not quietly omit a stage."""
    profiles = _profiles()
    assert set(profiles["feature"]) == set(_roster())


def test_every_profile_reaches_a_gate():
    """A profile with no approval gate would be an agent deciding on its own.

    The gates are 1.3 and 2.7. Every profile has to include at least one, because the
    record of who approved what is the reason this process exists.
    """
    gates = {"1.3", "2.7"}
    for name, stages in _profiles().items():
        assert gates & set(stages), f"profile {name} has no approval gate"


def test_intake_is_in_every_profile():
    """Nothing can run without a request record and a brief to work from."""
    for name, stages in _profiles().items():
        assert "0.1" in stages, f"profile {name} skips request intake"
        assert "1.1" in stages, f"profile {name} skips intent capture"


# --- the phase skills and the diagram agree with the roster ---


@pytest.mark.parametrize(
    "skill,stage",
    [
        ("intake-elicitation", "1.1"),
        ("functional-spec", "2.2"),
        ("bdd-scenarios", "2.5"),
        ("existing-system", "2.1"),
    ],
)
def test_each_phase_skill_names_the_stage_it_runs(skill, stage):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert stage in text, f"{skill} never names stage {stage}"


def test_the_diagram_node_ids_follow_the_stage_numbers():
    """`2.5` draws as `s25`, so a reader can map a node back to a stage."""
    text = (SKILLS / "progress-diagram" / "SKILL.md").read_text(encoding="utf-8")
    ids = set(re.findall(r"\bs(\d\d)\b", text))
    assert ids, "the diagram template declares no stage nodes"
    for node in ids:
        stage = f"{node[0]}.{node[1]}"
        assert stage in _roster(), f"the diagram draws s{node}, which is not stage {stage}"


def test_the_diagram_declares_all_four_states():
    """A skipped conditional stage has to be visibly considered, not missing."""
    text = (SKILLS / "progress-diagram" / "SKILL.md").read_text(encoding="utf-8")
    for state in ("done", "current", "pending", "skipped"):
        assert f"classDef {state}" in text, f"the diagram has no {state} style"


def test_the_agent_prompt_sends_the_model_to_the_router_first():
    """The prompt is the only thing read before any skill, so it has to route."""
    prompt = (SKILLS.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert "/skills/sdlc-workflow/SKILL.md" in prompt
