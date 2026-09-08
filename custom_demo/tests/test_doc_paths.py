"""The doc-path checker, pinned on the decisions that would silently be wrong.

`scripts/check_doc_paths.py` asserts that every repo path a comment or a doc cites is
on disk. Whether it catches a stale path is the easy half and one test covers it. The
half that decides whether the check is worth having is everything it declines to check:
a glob, a path on the agent's sandbox VM, a URL, an illustrative fence, an npm package.
Each of those, wrong, is either a false alarm that gets the check deleted or a silent
hole that lets the next rename through. So they are pinned one by one here.

Samples are written to `tmp_path` and scanned end to end - fence and comment extraction
included - against an index of the REAL tree, so "resolves" means the same thing it does
in CI.

The checker is a standalone script, not part of the package, so it is loaded by path and
registered in `sys.modules` before it executes: it defines dataclasses, and
`dataclasses` looks the defining module up by name while processing the class.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "check_doc_paths", ROOT / "scripts" / "check_doc_paths.py"
)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
sys.modules["check_doc_paths"] = checker
_SPEC.loader.exec_module(checker)

INDEX = checker.RepoIndex(ROOT)


def cited(text: str, tmp_path: Path, name: str = "sample.md") -> list[str]:
    """The tokens the checker reports as unresolvable in one sample file."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    found, _ = checker.violations_in_file(path, INDEX)
    return [v.token for v in found]


# --- it catches a stale path (the whole point) ---


def test_catches_a_module_that_moved(tmp_path: Path):
    text = "The judge helper lives in `custom_demo/assistant_evals.py`.\n"
    assert cited(text, tmp_path) == ["custom_demo/assistant_evals.py"]


def test_catches_a_stale_path_inside_a_copy_pasteable_command(tmp_path: Path):
    # The exact defect this was written for: a `pytest` line in AGENTS.md naming a test
    # file that is not in the repo, which fails the moment someone pastes it.
    text = "```bash\nuv run pytest custom_demo/tests/test_rag.py -q\n```\n"
    assert cited(text, tmp_path) == ["custom_demo/tests/test_rag.py"]


def test_reports_the_line_the_citation_is_on(tmp_path: Path):
    path = tmp_path / "sample.md"
    path.write_text("one\ntwo\nsee `custom_demo/gone.py`\n", encoding="utf-8")
    found, _ = checker.violations_in_file(path, INDEX)
    assert [(v.line, v.token) for v in found] == [(3, "custom_demo/gone.py")]


# --- the exclusions ---


def test_a_glob_is_not_a_path(tmp_path: Path):
    # `custom_demo/tests/*.js` is how CLAUDE.md names the node test suite. A glob has no
    # single path to stat, and an illustrative one may legitimately match nothing.
    text = "CI runs every `custom_demo/tests/*.js` and every `evals/*.md`, plus `docs/**/*.md`.\n"
    assert cited(text, tmp_path) == []


def test_a_sandbox_vm_path_is_remote_and_never_checked(tmp_path: Path):
    # These are paths on the agent's own VM, named in prompt text. They are correct as
    # written and none of them is in this repo.
    text = (
        "Seeded files land at `/workspace/data/sales.csv` and the skill the prompt names\n"
        "is `/skills/dashboard/SKILL.md`; the seed spec is written to `/tmp/seed.json`.\n"
    )
    assert cited(text, tmp_path) == []


def test_a_url_is_not_a_path(tmp_path: Path):
    text = (
        "Fetch `https://example.com/data/report.json` and read the app at\n"
        "`ui://meridian/signature.html`; docs live at http://localhost:3000/index.html.\n"
    )
    assert cited(text, tmp_path) == []


def test_a_language_tagged_fence_is_illustrative_and_is_skipped(tmp_path: Path):
    text = "```python\nfrom custom_demo.gone import thing  # custom_demo/gone.py\n```\n"
    assert cited(text, tmp_path) == []


def test_the_same_path_in_an_untagged_fence_is_checked(tmp_path: Path):
    # Untagged fences in these docs hold the repo map, which is a claim about the tree.
    text = "```\ncustom_demo/gone.py    a module that is not there\n```\n"
    assert cited(text, tmp_path) == ["custom_demo/gone.py"]


def test_prose_after_a_fence_closes_is_checked_again(tmp_path: Path):
    text = "```python\nimport x  # custom_demo/one.py\n```\n\nSee `custom_demo/two.py`.\n"
    assert cited(text, tmp_path) == ["custom_demo/two.py"]


def test_a_placeholder_is_not_a_path(tmp_path: Path):
    text = (
        "Skills are stored as `<name>/SKILL.md` (or `{name}/SKILL.md`), and an artifact\n"
        "as `.../report.html`.\n"
    )
    assert cited(text, tmp_path) == []


def test_a_vendored_or_generated_tree_is_not_checked(tmp_path: Path):
    # Present on a dev machine, absent in a fresh checkout: checking either way is wrong.
    text = (
        "Styles come from `../node_modules/streamdown/dist/index.js`; see `frontend/dist/x.js`.\n"
    )
    assert cited(text, tmp_path) == []


def test_prose_that_merely_contains_a_slash_is_not_a_path(tmp_path: Path):
    text = (
        "The demo reads 2/3 then 3/3, the theme is light/dark, every hook is sync/async,\n"
        "the panel is `EvalPanel` then `evals/EvalRunner`, and the evals/traffic targets\n"
        "both take the sync path.\n"
    )
    assert cited(text, tmp_path) == []


def test_a_first_segment_from_another_project_is_not_a_claim_about_this_tree(tmp_path: Path):
    # `app/tracing.py` is a file in a different repo, named in a docstring that says so;
    # `shiki/...` and `streamdown/...` are npm packages.
    text = (
        "Shape borrowed from google-adk-realtime-deepagents-example (`app/tracing.py`).\n"
        "Highlighting loads `shiki/langs/bash.mjs` and `streamdown/styles.css`.\n"
    )
    assert cited(text, tmp_path) == []


# --- the two tiers ---


RELATIVE_SHORTHANDS = [
    "tools/registry.py",  # -> custom_demo/runtime/tools/registry.py
    "core/ctx.py",  # -> custom_demo/core/ctx.py
    "chat/ReviewCard.tsx",  # -> frontend/src/components/chat/ReviewCard.tsx
    "lib/commands.ts",  # -> frontend/src/lib/commands.ts
    "provisioning/evals.py",
    "web/sandbox.py",
]


@pytest.mark.parametrize("token", RELATIVE_SHORTHANDS)
def test_a_relative_shorthand_resolves_by_path_suffix(token: str, tmp_path: Path):
    # This repo's prose cites modules relative to their package. Requiring the full path
    # would flag every one of them, which is the fastest way to get a check turned off.
    assert cited(f"See `{token}` for the details.\n", tmp_path) == []


def test_a_relative_shorthand_that_matches_nothing_is_still_caught(tmp_path: Path):
    # `tests/` is a directory in this tree, so this IS a claim about it - and false.
    assert cited("The command runs `tests/test_database.py`.\n", tmp_path) == [
        "tests/test_database.py"
    ]


def test_a_root_anchored_path_must_resolve_from_the_root_exactly(tmp_path: Path):
    # `tools/registry.py` passes as a shorthand; spelled from the root it is wrong, and
    # this is the tier that catches the whole package-rename class.
    assert cited("See `custom_demo/tools/registry.py`.\n", tmp_path) == [
        "custom_demo/tools/registry.py"
    ]


def test_a_leading_dot_slash_is_repo_root_relative(tmp_path: Path):
    text = "Run `./scripts/run_mcp_server.sh --tunnel`, not `./scripts/seed_assistants.py`.\n"
    assert cited(text, tmp_path) == ["scripts/seed_assistants.py"]


def test_a_dot_directory_is_checked_only_when_this_repo_owns_it(tmp_path: Path):
    # `.github/` is ours, so a stale workflow path is a defect. `.claude/` is partly
    # machine-local, so it is not a root anchor and is left alone.
    text = "Gates live in `.github/workflows/nope.yml`; the skill is `.claude/skills/x/SKILL.md`.\n"
    assert cited(text, tmp_path) == [".github/workflows/nope.yml"]


# --- what gets scanned, per language ---


def test_python_comments_and_docstrings_are_scanned(tmp_path: Path):
    source = '"""Module doc citing custom_demo/one.py."""\n\n# See custom_demo/two.py\nx = 1\n'
    assert cited(source, tmp_path, "sample.py") == ["custom_demo/one.py", "custom_demo/two.py"]


def test_a_python_string_literal_is_not_scanned(tmp_path: Path):
    # Prompt text and sandbox fixtures are full of string literals holding paths that
    # live on the agent's VM. Reading them as citations would flag the lot.
    source = 'PROMPT = "Open custom_demo/not_a_real_module.py in the VM"\n'
    assert cited(source, tmp_path, "sample.py") == []


def test_typescript_comments_are_scanned_both_ways(tmp_path: Path):
    source = "/* block: custom_demo/one.py */\n// line: custom_demo/two.py\nexport const a = 1;\n"
    assert cited(source, tmp_path, "sample.ts") == ["custom_demo/one.py", "custom_demo/two.py"]


def test_a_double_slash_inside_a_string_is_not_the_start_of_a_comment(tmp_path: Path):
    # Without a lexer, the `//` in a URL swallows the rest of the line as "comment"
    # text, and any path later on that line goes unchecked.
    source = 'const u = "https://x.dev/a"; // see custom_demo/gone.py\n'
    assert cited(source, tmp_path, "sample.ts") == ["custom_demo/gone.py"]


def test_a_path_in_typescript_code_is_not_a_citation(tmp_path: Path):
    source = 'import { a } from "@/lib/api";\nfetch("/sandbox-file?path=custom_demo/x.py");\n'
    assert cited(source, tmp_path, "sample.ts") == []


# --- the allowlist ---


def test_an_allowlist_entry_suppresses_exactly_its_own_citation(tmp_path: Path):
    path = tmp_path / "sample.md"
    path.write_text("`custom_demo/gone.py` and `custom_demo/other.py`\n", encoding="utf-8")
    key = (checker.label_for(path), "custom_demo/gone.py")
    checker.ALLOWLIST[key] = "unit test"
    try:
        found, used = checker.violations_in_file(path, INDEX)
    finally:
        del checker.ALLOWLIST[key]

    assert [v.token for v in found] == ["custom_demo/other.py"]
    assert used == {key}


def test_every_allowlist_entry_carries_a_reason():
    assert all(reason.strip() for reason in checker.ALLOWLIST.values())


def test_an_allowlist_entry_that_matches_nothing_fails_the_check(capsys):
    # The RUF100 bargain: a waiver cannot outlive the thing it waived.
    key = ("docs/nowhere.md", "custom_demo/gone.py")
    checker.ALLOWLIST[key] = "unit test"
    try:
        code = checker.main([])
    finally:
        del checker.ALLOWLIST[key]

    assert code == 1
    assert "match nothing" in capsys.readouterr().out


# --- the repo itself ---


def test_every_cited_path_in_this_repo_resolves():
    # The CI gate, runnable from pytest: a stale citation fails here too.
    assert checker.main([]) == 0
