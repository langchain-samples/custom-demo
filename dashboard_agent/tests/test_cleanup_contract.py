"""The `ls_artifacts` cleanup contract, pinned across all three of its sides.

Deleting an assistant cascade-deletes everything its setup run created in the
customer's LangSmith workspace. The handles travel as a dict written by
`assistant_setup`, stored in the assistant's metadata, POSTed back verbatim by
the SPA, and read key-by-key by `/cleanup`.

Nothing enforced that those three agreed. And the failure is silent by
construction: `/cleanup`'s `_try` skips a falsy handle, so a renamed key reads as
"there was no dataset" rather than as an error. A dataset, a prompt, an
evaluator or a review queue is then left behind in the customer's workspace, with
a success response on both sides. This file is the enforcement.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETUP = REPO / "dashboard_agent" / "provisioning" / "setup.py"
CLEANUP = REPO / "dashboard_agent" / "web" / "cleanup.py"
API_TS = REPO / "frontend" / "src" / "lib" / "api.ts"

# The contract. A key added here has to be added to all three sides; a key
# removed from any side without this list changing fails the tests below.
KEYS = frozenset(
    {
        "workspace",
        "project",
        "agent_repo",
        "skills_repo",
        "skills",
        "eval_dataset",
        "eval_rule_id",
        "eval_evaluator_id",
        "eval_judge_prompt",
        "annotation_queue",
    }
)


def _written_keys() -> set[str]:
    """The keys `prepare_assistant` puts in the manifest."""
    body = SETUP.read_text(encoding="utf-8")
    start = body.index('"ls_artifacts": {')
    # The dict literal ends at the first line that closes it at this indent.
    end = body.index("\n        },", start)
    return set(re.findall(r'^\s*"([a-z_]+)":', body[start:end], re.M)) - {"ls_artifacts"}


def _cleanup_section() -> str:
    body = CLEANUP.read_text(encoding="utf-8")
    start = body.index("async def cleanup(")
    return body[start : body.index("\ndef _delete_eval_rule", start)]


def _guard_keys() -> set[str]:
    """Keys read as `body.get(...)`, which is what decides IF a delete runs.

    The guard is the half that matters: `_try` skips a falsy handle, so a key
    only wrong here silently deletes nothing.
    """
    return set(re.findall(r'body\.get\("([a-z_]+)"\)', _cleanup_section()))


def _accessor_keys() -> set[str]:
    """Keys read as `body[...]`, i.e. the handle actually passed to the delete."""
    return set(re.findall(r'body\["([a-z_]+)"\]', _cleanup_section()))


def _read_keys() -> set[str]:
    """Keys `/cleanup` guards on. Deliberately NOT unioned with the accessors.

    An earlier version of this file took the union, which made it useless: every
    handle is read twice, once to guard and once to use, so renaming one of the
    two still matched the other and the test passed. Verified by mutation.
    """
    return _guard_keys()


def _declared_keys() -> set[str]:
    """The fields the SPA's `LsArtifacts` interface declares."""
    body = API_TS.read_text(encoding="utf-8")
    start = body.index("export interface LsArtifacts {")
    end = body.index("\n}", start)
    # Skip doc comments; take `name?:` / `name:` at the top level of the interface.
    return set(re.findall(r"^  ([a-z_]+)\??:", body[start:end], re.M))


def test_setup_writes_exactly_the_contract():
    assert _written_keys() == set(KEYS)


def test_cleanup_reads_exactly_the_contract():
    assert _read_keys() == set(KEYS)


def test_the_spa_declares_exactly_the_contract():
    """The SPA POSTs this object verbatim, so a field it cannot type never arrives."""
    assert _declared_keys() == set(KEYS)


def test_each_guard_and_its_handle_name_the_same_key():
    """A `_try` that guards on one key and deletes by another is a silent no-op.

    Every accessor must also be a guard. `workspace` and `skills` are guards
    only, by shape: one is forwarded to a helper, the other is iterated.
    """
    assert _accessor_keys() <= _guard_keys()
    assert _guard_keys() - _accessor_keys() == {"workspace", "skills"}


@pytest.mark.parametrize("key", sorted(KEYS))
def test_every_handle_is_written_read_and_declared(key: str):
    """Named per key, so a break says WHICH artifact would be orphaned."""
    assert key in _written_keys(), f"{key} is never written into ls_artifacts"
    assert key in _read_keys(), f"{key} is written but /cleanup never deletes it"
    assert key in _declared_keys(), f"{key} is not declared on the SPA's LsArtifacts"
