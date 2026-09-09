"""No em-dashes in text the model or the user reads.

The agent is told never to use them, and the instruction only carries weight if the
prompt containing it obeys its own rule. There is a mechanical check for this on the
frontend and the docs (`frontend/scripts/check-no-emdash.mjs`), but it scans no Python,
so the prompt could and did ship four of them inside the very block that forbids them.

Scope is every module under `custom_demo/`, because prose that travels is not confined
to the handful of modules that obviously assemble a prompt: a `ToolSpec.guidance` string
is appended to the system prompt, and `provisioning/traffic.py` writes synthetic trace
content a customer reads in LangSmith. `custom_demo/tests/` is excluded on purpose:
fixtures and assertions are read by whoever is working on the source, never sent to a
model or shown to a customer.

Docstrings and comments are exempt for the same reason, with one exception. A function
decorated with `@tool` has its docstring lifted verbatim into the tool description sent
to the model, so that docstring travels and is checked like any other literal. Only
string literals that travel are checked, found by walking the AST rather than grepping,
so the two cannot be confused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

EM_DASH = "—"

# The decorator that turns a docstring into model-facing text. Matched by bare name, so
# `@tool`, `@tool(...)` and `@langchain.tools.tool` all count; this repo writes the bare
# `@tool` form in every case today.
TOOL_DECORATOR = "tool"

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TESTS = SELF.parent


def _sources() -> list[str]:
    """Every module whose string literals reach a model or a user.

    That is all of `custom_demo/` bar the test package. This module is named again
    beyond that exclusion rather than left to it: it defines `EM_DASH` as a literal, so
    scanning its own source would fail unconditionally, and narrowing the test-package
    exclusion later must not quietly turn that on.
    """
    return sorted(
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*.py")
        if TESTS not in path.parents and path != SELF
    )


SOURCES = _sources()


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The final name of each decorator on `node`.

    Args:
        node: The function definition whose decorator list to read.

    Returns:
        One name per decorator: `tool` for `@tool`, for `@tool(...)`, and for a dotted
        `@langchain.tools.tool`.
    """
    names: set[str] = set()
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)

    return names


def _exempt_docstring_ids(tree: ast.AST) -> set[int]:
    """Every docstring the walk skips, which is every one except a `@tool` function's.

    Args:
        tree: The parsed module.

    Returns:
        The ids of the string nodes to leave unchecked.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue

        value = node.body[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue

        is_tool = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            TOOL_DECORATOR in _decorator_names(node)
        )
        if is_tool:
            continue

        out.add(id(value))

    return out


@pytest.mark.parametrize("relative", SOURCES)
def test_no_em_dash_in_text_that_travels(relative: str):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _exempt_docstring_ids(tree)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and EM_DASH in node.value
    ]
    assert not offenders, (
        f"{relative} has an em-dash in a string literal at line(s) {offenders}. "
        "The prompt tells the model never to use one, so the prompt cannot either."
    )
