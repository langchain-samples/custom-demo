"""The house blank-line rule's checker, on the cases that would silently be wrong.

`scripts/check_blank_after_block.py` enforces: after an indented block ends, the next
statement at the same indentation needs a blank line in front of it. The logic that
matters is the logic that says NO - `elif`/`else`/`except`/`finally` are clauses of the
statement they follow, not statements after it, and a comment introducing the next
statement takes the blank line above itself rather than below. Those are exactly the
places where a regex implementation reports a false positive, so they are pinned here.

The checker is a standalone script, not part of the package, so it is loaded by path.
It has to be registered in `sys.modules` before it executes: it defines a dataclass,
and `dataclasses` looks the defining module up by name while processing the class.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "check_blank_after_block", ROOT / "scripts" / "check_blank_after_block.py"
)
assert _SPEC and _SPEC.loader
checker = importlib.util.module_from_spec(_SPEC)
sys.modules["check_blank_after_block"] = checker
_SPEC.loader.exec_module(checker)


def lines_of(source: str) -> list[int]:
    """The 1-based lines where the checker wants a blank line inserted."""
    text = textwrap.dedent(source).lstrip("\n")
    return [v.line for v in checker.violations_in_source(text, Path("sample.py"))]


def fixed(source: str, tmp_path: Path) -> str:
    """Round-trip `source` through --fix and return the result."""
    text = textwrap.dedent(source).lstrip("\n")
    path = tmp_path / "sample.py"
    path.write_text(text, encoding="utf-8")
    checker.apply_fix(path, {v.line for v in checker.violations_in_file(path)})
    return path.read_text(encoding="utf-8")


# --- the rule itself ---


def test_flags_statement_straight_after_a_block():
    source = """
        def f(raw):
            if raw is None:
                return ""
            allowed = names(raw)
            return allowed
    """
    assert lines_of(source) == [4]


def test_accepts_a_blank_line_after_the_block():
    source = """
        def f(raw):
            if raw is None:
                return ""

            allowed = names(raw)
            return allowed
    """
    assert lines_of(source) == []


# Every compound statement the rule names, each as a body whose block is followed
# immediately by another statement at the block's own indentation.
COMPOUND_BODIES = [
    "for i in xs:\n        use(i)\n    done = True",
    "while ok():\n        step()\n    done = True",
    "with open(p) as fh:\n        read(fh)\n    done = True",
    "try:\n        risky()\n    except ValueError:\n        pass\n    done = True",
    "async for i in xs:\n        await use(i)\n    done = True",
    "async with lock:\n        await go()\n    done = True",
]


@pytest.mark.parametrize("body", COMPOUND_BODIES)
def test_applies_to_every_compound_statement(body: str):
    source = f"async def f():\n    {body}\n    return done\n"
    violations = checker.violations_in_source(source, Path("sample.py"))
    assert [v.text for v in violations] == ["done = True"], source


# --- the exclusions: clauses are not "the next statement" ---


def test_elif_and_else_never_want_a_blank_line_before_them():
    source = """
        def f(x):
            if x == 1:
                return "one"
            elif x == 2:
                return "two"
            else:
                return "many"
    """
    assert lines_of(source) == []


def test_except_and_finally_never_want_a_blank_line_before_them():
    source = """
        def f():
            try:
                risky()
            except ValueError:
                handle()
            except KeyError:
                handle()
            else:
                fine()
            finally:
                close()
    """
    assert lines_of(source) == []


def test_a_block_that_ends_its_parent_needs_nothing_after_it():
    source = """
        def f(xs):
            for x in xs:
                if x:
                    return x
    """
    assert lines_of(source) == []


def test_a_nested_block_ending_on_its_parents_last_line_is_measured_by_end_lineno():
    source = """
        def f(x):
            try:
                go()
            except ValueError: pass
            after = 1
            return after
    """
    assert lines_of(source) == [5]


def test_def_and_class_are_left_to_ruffs_own_spacing_rules():
    source = """
        def outer():
            def inner():
                return 1
            value = inner()
            return value
    """
    assert lines_of(source) == []


# --- comments and decorators: where the blank line lands ---


def test_the_blank_line_goes_above_a_comment_that_introduces_the_next_statement():
    source = """
        def f(raw):
            if raw is None:
                return ""
            # Everything below needs the parsed form.
            allowed = names(raw)
            return allowed
    """
    # Line 4 is the comment, not line 5 where the statement is.
    assert lines_of(source) == [4]


def test_a_comment_already_separated_from_the_block_is_not_a_violation():
    source = """
        def f(raw):
            if raw is None:
                return ""

            # Everything below needs the parsed form.
            allowed = names(raw)
            return allowed
    """
    assert lines_of(source) == []


def test_a_deeper_indented_comment_trails_the_block_it_sits_in():
    source = """
        def f(raw):
            if raw is None:
                return ""
                # Nothing else to do here.
            allowed = names(raw)
            return allowed
    """
    # The comment belongs to the `if` body, so the blank line goes below it (line 5),
    # not above it - pulling it along would move it out of the block it explains.
    assert lines_of(source) == [5]


def test_the_blank_line_goes_above_the_first_decorator():
    source = """
        def outer():
            if skip():
                return None
            @cache
            @traced
            def inner():
                return 1

            return inner
    """
    assert lines_of(source) == [4]


def test_a_multiline_block_is_measured_to_its_real_last_line():
    source = """
        def f(xs):
            if xs:
                total = sum(
                    x
                    for x in xs
                )
            count = len(xs)
            return count
    """
    assert lines_of(source) == [7]


def test_match_is_treated_as_a_block():
    source = """
        def f(x):
            match x:
                case 1:
                    y = "one"
                case _:
                    y = "many"
            return y
    """
    assert lines_of(source) == [7]


# --- --fix ---


def test_fix_inserts_the_blank_lines_and_leaves_nothing_behind(tmp_path: Path):
    source = """
        def f(raw):
            if raw is None:
                return ""
            allowed = names(raw)
            for a in allowed:
                use(a)
            return allowed
    """
    out = fixed(source, tmp_path)
    # A blank line after each block, and none before `for`: the rule is about what
    # follows a block, not about what precedes one.
    assert out == (
        "def f(raw):\n"
        "    if raw is None:\n"
        '        return ""\n'
        "\n"
        "    allowed = names(raw)\n"
        "    for a in allowed:\n"
        "        use(a)\n"
        "\n"
        "    return allowed\n"
    )
    assert ast.parse(out)
    assert checker.violations_in_source(out, Path("sample.py")) == []


def test_fix_is_idempotent(tmp_path: Path):
    source = """
        def f(xs):
            for x in xs:
                use(x)
            n = len(xs)
            return n
    """
    once = fixed(source, tmp_path)
    twice = fixed(once, tmp_path)
    assert once == twice
