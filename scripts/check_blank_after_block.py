#!/usr/bin/env python3
"""Fails if an indented block is not followed by a blank line.

The house rule, in the owner's words: "a personal preference i like after any indented
code is to have a newline ... any if/for/except, etc." Concretely: once a compound
statement's body ends, a statement that follows at the SAME indentation as that
compound statement must be preceded by a blank line.

    if raw is None:          if raw is None:
        return ""                return ""
    allowed = names(raw)
                             allowed = names(raw)
         BAD                        GOOD

It needs its own checker because ruff has no rule for it: E301/E302/E303 are about
methods and top-level defs, not about the statement after an `if`. And `ruff format`
PRESERVES manually added blank lines inside a function body, so what this inserts
survives formatting rather than being undone by it.

Modelled on `frontend/scripts/check-no-emdash.mjs`: a standalone checker, wired into
the CI lint step, that prints `path:line` per violation and exits non-zero.

    uv run python scripts/check_blank_after_block.py           # check (exit 1 if any)
    uv run python scripts/check_blank_after_block.py --fix     # insert the blank lines

Built on the AST rather than on regex or indentation counting, which buys three things
for free:

    * `elif` / `else` / `except` / `finally` are parts of the SAME node, never
      siblings of the block they follow, so they can never be asked for a blank line
      in front of them. (`ast` models `elif` as a nested `If` inside `orelse`, and it
      is the only element there, so sibling-walking never sees it as a "next".)
    * A block that is the last statement in its parent has no next sibling, so it
      needs no blank line after it.
    * A nested block that ends on the same line as its parent is bounded by
      `end_lineno`, not by guessing from the text.

The ambiguous cases, and what this decides:

    * A COMMENT between the block and the next statement. The comment introduces what
      follows, so the blank line belongs BEFORE the comment, and a comment block that
      is already separated from the block by a blank line is fine. Comments INDENTED
      DEEPER than the next statement are read as trailing remarks belonging to the
      block that just ended, so they are not pulled along with the next statement.
    * DECORATORS. A decorated `def`/`class` starts at its first decorator, not at the
      `def` line, so the blank line goes above the decorator.
    * MULTI-LINE statements. The block's extent is `end_lineno`, so a block whose last
      statement wraps over several lines is measured to its true last line.
    * `def` and `class` are NOT treated as blocks that require a blank line after
      them; ruff's E301/E302/E303 already own the spacing around definitions, and
      requiring one here would fight the formatter.
    * `match` IS treated as a block (it is a compound statement like any other), even
      though this repo currently has none, so the first one to land is covered.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The Python this repo owns. `custom_demo` and `dashboard_agent` are the same package
# under its old and new name; whichever exists is scanned, so a rename needs no edit
# here. `chat-langchain-lite/` is deliberately absent: it is a gitignored sibling
# project, not ours to reformat.
DEFAULT_TARGETS = ("custom_demo", "dashboard_agent", "scripts", "evals", "mcp_demo_server")

# Never walked, even when they sit inside a target.
SKIP_DIRS = frozenset({".venv", ".git", "__pycache__", "node_modules", ".langgraph_api"})

# Compound statements the rule applies to: if/for/while/with/try, their async forms,
# and match. Deliberately NOT FunctionDef/AsyncFunctionDef/ClassDef (see docstring).
BLOCK_STATEMENTS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.Match,
)

# Fields holding a list of sibling statements. `handlers` (of `Try`) and `cases` (of
# `Match`) are excluded on purpose: their entries are clauses of the same statement,
# not siblings that follow it.
BODY_FIELDS = ("body", "orelse", "finalbody")


@dataclass(frozen=True)
class Violation:
    """One missing blank line: `line` is where the blank line has to be inserted."""

    path: Path
    line: int
    text: str


def _is_blank(line: str) -> bool:
    """True for a line that is empty or only whitespace."""
    return not line.strip()


def _indent(line: str) -> int:
    """Number of leading whitespace characters."""
    return len(line) - len(line.lstrip())


def _effective_start(lines: list[str], node: ast.stmt) -> int:
    """The first line that introduces `node`: its decorators, then the comments above.

    A blank line inserted at this line lands above the whole introduction rather than
    between a comment and the statement it describes. Comments indented deeper than the
    statement are left where they are: they trail the block that just ended.
    """
    start = node.lineno
    decorators = getattr(node, "decorator_list", [])
    if decorators:
        start = min(start, min(d.lineno for d in decorators))

    own_indent = _indent(lines[start - 1])
    probe = start - 1
    while probe >= 1:
        line = lines[probe - 1]
        if not line.strip().startswith("#") or _indent(line) > own_indent:
            break

        start = probe
        probe -= 1

    return start


def violations_in_source(source: str, path: Path) -> list[Violation]:
    """Every missing blank line in one module's source text."""
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    found: list[Violation] = []

    for node in ast.walk(tree):
        for field in BODY_FIELDS:
            siblings = getattr(node, field, None)
            # `IfExp.orelse` is a bare expression, not a body; skip anything that is
            # not a statement list.
            if not isinstance(siblings, list):
                continue

            for current, following in zip(siblings, siblings[1:], strict=False):
                if not isinstance(current, BLOCK_STATEMENTS):
                    continue

                if not isinstance(following, ast.stmt):
                    continue

                start = _effective_start(lines, following)
                end = current.end_lineno or current.lineno
                between = lines[end : start - 1]
                if any(_is_blank(line) for line in between):
                    continue

                found.append(Violation(path, start, lines[start - 1].strip()))

    return sorted(found, key=lambda v: v.line)


def violations_in_file(path: Path) -> list[Violation]:
    """Every missing blank line in one file on disk."""
    return violations_in_source(path.read_text(encoding="utf-8"), path)


def apply_fix(path: Path, insert_before: set[int]) -> None:
    """Insert a blank line above each given line number, bottom-up so they don't shift."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for line_no in sorted(insert_before, reverse=True):
        lines.insert(line_no - 1, "\n")

    path.write_text("".join(lines), encoding="utf-8")


def python_files(targets: list[str]) -> list[Path]:
    """Every `.py` file under the given targets, which may name files or directories."""
    out: list[Path] = []
    for target in targets:
        base = Path(target)
        if not base.is_absolute():
            base = ROOT / target

        if not base.exists():
            continue

        if base.is_file():
            out.append(base)
            continue

        for path in sorted(base.rglob("*.py")):
            if SKIP_DIRS.isdisjoint(path.parts):
                out.append(path)

    return out


def main(argv: list[str] | None = None) -> int:
    """Check (or fix) every target, and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets",
        nargs="*",
        default=None,
        help="files or directories to scan (default: this repo's Python)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="insert the missing blank lines instead of only reporting them",
    )
    args = parser.parse_args(argv)

    files = python_files(args.targets or list(DEFAULT_TARGETS))
    offenders: list[Violation] = []
    fixed_files = 0
    for path in files:
        found = violations_in_file(path)
        if not found:
            continue

        if args.fix:
            apply_fix(path, {v.line for v in found})
            fixed_files += 1
            offenders.extend(found)
            continue

        offenders.extend(found)

    if args.fix:
        remaining = [v for path in files for v in violations_in_file(path)]
        print(
            f"inserted {len(offenders)} blank line(s) across {fixed_files} file(s); "
            f"{len(remaining)} violation(s) remain"
        )
        return 1 if remaining else 0

    if offenders:
        print(f"\nx missing blank line after an indented block in {len(offenders)} location(s):\n")
        for violation in offenders:
            rel = violation.path.relative_to(ROOT)
            print(f"  {rel}:{violation.line}: {violation.text[:100]}")

        print(
            "\nAfter an if/for/while/with/try body ends, leave a blank line before the "
            "next statement at the same indentation.\n"
            "Run `uv run python scripts/check_blank_after_block.py --fix` to insert them.\n"
        )
        return 1

    print(f"ok  blank line after every indented block ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
