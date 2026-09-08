"""The tool vocabulary, pinned between the backend catalogue and the SPA.

`tools/registry.py` calls itself the single source of truth and its docstring
claims adding a capability needs "no changes to the frontend list". That was
already false: the SPA re-types the tool names in several hand-written maps, and
a tool added to the catalogue without touching them renders as a generic icon
with its raw identifier for a label.

Serving those maps from `GET /tools` is not possible as they stand, because they
also cover the deepagents built-ins (`task`, `execute`, `read_file`, ...),
which the registry does not know about and should not. So the guarantee is made
here instead: every catalogue tool must appear in every map that keys off a tool
name, and the failure names the tool and the file.

The built-ins are deliberately not checked. They come from a dependency, so a
list of them here would be its own copy of someone else's vocabulary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dashboard_agent.runtime.tools.registry import TOOL_REGISTRY

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

CATALOGUE = sorted(spec.id for spec in TOOL_REGISTRY)

# Tools deepagents provides, which these maps also label. Not a second copy of the
# catalogue: these names come from the dependency, and the test below needs to know
# them only to tell them apart from a name nothing defines.
BUILTINS = frozenset(
    {
        "task",
        "execute",
        "read_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "grep",
        "delete",
        # From langchain-quickjs, bound only when DYNAMIC_SUBAGENTS is on.
        "eval",
    }
)

# Each map that is keyed by tool name, and how to read its keys out.
# `lib/agentGraph.ts` is deliberately absent: it assigns lanes with a laneFor()
# function rather than a keyed map, and it falls back to a real lane, so an
# unknown tool there is placed rather than unlabelled.
MAPS = {
    "lib/toolLabels.ts": (r"export const TOOL_LABELS[^{]*\{", r"^  ([a-z_]+):"),
    "components/chat/helpers.ts": (r"const TOOL_ICONS[^{]*\{", r"^  ([a-z_]+):"),
}


def _keys(relative: str) -> set[str]:
    """Tool names a given frontend map declares."""
    body = (FRONTEND / relative).read_text(encoding="utf-8")
    opener, key_pattern = MAPS[relative]
    match = re.search(opener, body, re.M)
    if match is None:
        pytest.skip(f"{relative}: map not found, shape changed")
    tail = body[match.end() :]
    return set(re.findall(key_pattern, tail[: tail.index("\n};")], re.M))


@pytest.mark.parametrize("relative", sorted(MAPS))
@pytest.mark.parametrize("tool", CATALOGUE)
def test_every_catalogue_tool_is_known_to_the_frontend(tool: str, relative: str):
    """A catalogue tool the SPA has never heard of renders as a raw identifier."""
    assert tool in _keys(relative), (
        f"{tool} is in TOOL_REGISTRY but not in frontend/src/{relative}. "
        "Add it there, or the chip shows the bare tool name."
    )


@pytest.mark.parametrize("relative", sorted(MAPS))
def test_the_frontend_names_no_tool_the_catalogue_dropped(relative: str):
    """The other direction: a removed tool leaves dead entries behind.

    Checked as a set difference rather than against a list of known-removed names: a
    fixed denylist stops catching anything the moment those names are gone, which is
    the state it was in. The deepagents built-ins are excluded because they come from
    a dependency, so enumerating them here would be a copy of someone else's
    vocabulary.
    """
    stale = _keys(relative) - set(CATALOGUE) - BUILTINS
    assert not stale, (
        f"frontend/src/{relative} lists tools that are in neither the catalogue nor the "
        f"deepagents built-ins: {sorted(stale)}. Remove them, or add a new catalogue "
        "tool to TOOL_REGISTRY."
    )
