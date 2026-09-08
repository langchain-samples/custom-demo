"""No em-dashes in text the model or the user reads.

The agent is told never to use them, and the instruction only carries weight if the
prompt containing it obeys its own rule. There is a mechanical check for this on the
frontend and the docs (`frontend/scripts/check-no-emdash.mjs`), but it scans no Python,
so the prompt could and did ship four of them inside the very block that forbids them.

Docstrings and comments are exempt: they are for whoever is reading the source, where
the em-dash is this repo's house style. Only string literals that travel are checked,
found by walking the AST rather than grepping, so the two cannot be confused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

EM_DASH = "—"

# Modules whose string literals reach a model or a user.
SOURCES = [
    "runtime/prompt.py",
    "runtime/agent.py",
    "provisioning/setup.py",
    "provisioning/evals.py",
    "voice/session.py",
]

ROOT = Path(__file__).resolve().parents[1]


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, so the walk can skip them."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    out.add(id(value))

    return out


@pytest.mark.parametrize("relative", SOURCES)
def test_no_em_dash_in_text_that_travels(relative: str):
    path = ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_ids(tree)
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
