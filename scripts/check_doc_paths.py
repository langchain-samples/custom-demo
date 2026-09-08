#!/usr/bin/env python3
"""Fails if a comment or a doc cites a repo path that is not on disk.

Every "see `custom_demo/core/ctx.py`" is a claim a machine can check, and this repo kept
breaking them: a package rename moved most of the modules into subpackages and the
sentences pointing at them kept the old spelling, so a reader following a citation landed
nowhere and a documented `pytest` command failed when pasted. The class of defect is
mechanical, so the fix is mechanical.

    uv run python scripts/check_doc_paths.py            # check (exit 1 if any)
    uv run python scripts/check_doc_paths.py AGENTS.md  # only these sources

There is no `--fix`. A stale path has no single right answer: one rename turned a module
into a package (`voice.py` to `voice/session.py`), another pushed a subpackage down a level
(`tools/registry.py` to `runtime/tools/registry.py`), and a third renamed the module as well
as moving it (`assistant_evals.py` to `provisioning/evals.py`). Guessing between those three
shapes would rewrite prose wrongly and quietly. This module is scanned like any other, which
is why the examples above are spelled as the destinations rather than the dead paths.

What is scanned
---------------

* The Markdown this repo maintains: `AGENTS.md`, `README.md`, `CLAUDE.md`, `docs/*.md`,
  `evals/README.md`.
* `#` comments and docstrings in `custom_demo/`, `evals/`, `scripts/` - found with
  `tokenize` and `ast`, never by grepping, so an ordinary string literal is not mistaken
  for a comment. That matters: `runtime/prompt.py` and the sandbox tests are full of
  string literals holding paths on the agent's VM, and none of them are claims about
  this repo.
* `//` and `/* */` comments in `frontend/src/**/*.{ts,tsx,css}`, extracted by a small
  state machine that knows about quotes and template literals, so a URL inside a string
  never reads as the start of a comment.

Precision over recall, deliberately
-----------------------------------

A checker that cries wolf gets deleted, so every ambiguous case is resolved by NOT
checking. Two tiers do the work:

1. A token whose first segment is a top-level directory of this repo (`ROOT_ANCHORS`)
   is a claim about a path from the repo root, and must resolve there exactly. This is
   the tier that catches the whole rename class, since those citations are all written
   `custom_demo/...`.
2. Any other relative token is checked only when its first segment names a directory
   that exists SOMEWHERE in this tree, and it passes if it is a path suffix of any file
   in the tree. So `tools/registry.py` resolves against
   `custom_demo/runtime/tools/registry.py` and `chat/ReviewCard.tsx` against
   `frontend/src/components/chat/ReviewCard.tsx` - the shorthand this repo's prose
   actually uses - while `app/tracing.py` (a file in another project, named in a
   docstring that says so) and `shiki/langs/bash.mjs` (an npm package) are not claims
   about this tree and are never checked.

Everything below is skipped, and here is why each one is not a defect:

* **Dot-prefixed paths**, which are module specifiers rather than repo paths
  (`./orbPalette.ts`, `../node_modules/...`), with one exception: a leading dot that
  belongs to a top-level directory of this repo, so `.github/workflows/ci.yml` is
  checked. `.claude/` is deliberately NOT a root anchor, since parts of it are
  machine-local and would fail a fresh checkout.
* **Absolute paths** (`/workspace/data/sales.csv`, `/skills/dashboard/SKILL.md`,
  `/tmp/seed.json`). These are REMOTE: they name the agent's sandbox VM or its mounted
  skills bundle, not this repo, and the prompt text that mentions them is correct as
  written. Checking them would fail on every prompt in the repo.
* **URLs.** A token's characters stop at `:`, so `https://x.com/a.json` and
  `ui://meridian/signature.html` arrive as `//x.com/a.json` and `//meridian/...`, which
  the absolute-path rule already drops.
* **Globs and placeholders** - anything holding `*`, `<`, `>`, `{`, `}`, `?` or `..`
  (`custom_demo/tests/*.js`, `<name>/SKILL.md`, `.../report.html`,
  `../node_modules/streamdown/dist/index.js`). A glob is not a path, and a glob written
  illustratively may legitimately match nothing, so statting or globbing it would report
  a defect that is not one.
* **Fenced code blocks tagged with a language** (```` ```python ````, ```` ```jsonc ````
  and friends). Those fences hold illustrative source: an import path or a config
  snippet chosen to show a shape, not to cite this layout. Untagged fences and
  ```` ```bash ````/```` ```sh ````/```` ```text ```` fences ARE checked, because in
  these docs they hold the repo map and the commands a person actually runs - which is
  exactly where a stale path bites (a `pytest` line naming a deleted test file fails
  when someone copy-pastes it).
* **Build output and vendored trees** (`node_modules`, `.venv`, `dist`, `__pycache__`,
  and the local server state in `.langgraph_api`). They exist on a dev machine and not
  in a fresh CI checkout, so checking them would either fail in CI or pass locally by
  accident.
* **Anything not ending in a known extension** (`SOURCE_EXTS`). Two thirds of the slashes
  in this repo's prose are not paths at all - "2/3 passing", "light/dark", "sync/async" -
  and the extension is what separates them cheaply. Directory references go with them:
  checking those was tried and immediately produced two false positives, `evals/EvalRunner`
  (prose naming a component, whose file is `EvalRunner.tsx`) and `evals/traffic` (prose
  naming the two eval and traffic targets). Both are legible to a reader and neither is a
  path, so extensionless tokens are prose here by default.

The allowlist is a last resort and each entry carries a reason; an entry that stops
matching anything is itself a failure, so a stale waiver cannot survive (the same
bargain ruff's `RUF100` makes about `# noqa`).
"""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Top-level directories this repo owns. A token starting with one of these is read as a
# path from the repo root and must resolve there exactly.
ROOT_ANCHORS = frozenset(
    {"custom_demo", "frontend", "evals", "scripts", "docs", "mcp_demo_server", ".github"}
)

# Extensions a citation may end in. `.sh` is here beyond the source languages because
# `scripts/run_mcp_server.sh` is cited in AGENTS.md and README.md and is checkable.
SOURCE_EXTS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".md",
    ".yml",
    ".html",
    ".css",
    ".sh",
)

# Never walked when indexing, and never checked when named in a token.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        "dist",
        ".langgraph_api",
        ".pytest_cache",
        ".ruff_cache",
        "chat-langchain-lite",
    }
)

# Characters that make a token a glob or a placeholder rather than a path.
PLACEHOLDER_CHARS = ("*", "<", ">", "{", "}", "?", "..")

# Fence info strings whose contents ARE checked. Anything else (```python, ```jsonc, …)
# is illustrative source and is skipped. The empty string is an untagged fence.
CHECKED_FENCES = frozenset({"", "bash", "sh", "shell", "console", "text", "txt"})

# Markdown this repo maintains, plus the trees whose comments are scanned.
MARKDOWN_SOURCES = ("AGENTS.md", "README.md", "CLAUDE.md", "evals/README.md")
MARKDOWN_GLOBS = ("docs/*.md",)
PYTHON_TREES = ("custom_demo", "evals", "scripts")
FRONTEND_TREE = "frontend/src"
FRONTEND_EXTS = (".ts", ".tsx", ".css")

# Genuinely unresolvable citations, keyed by (source file, token), valued by the reason.
# Empty, and worth keeping that way: every entry is a citation a reader cannot follow. The
# shape, when one is unavoidable:
#
#     ("docs/<the-doc>.md", "custom_demo/<gone>.py"): "documents the pre-1.0 layout",
#
# An entry that stops matching anything fails the check, so a fixed citation cannot leave a
# waiver behind.
ALLOWLIST: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class Violation:
    """One citation of a path that is not in the tree."""

    source: str
    line: int
    token: str


@dataclass(frozen=True)
class Chunk:
    """A run of prose to scan for path tokens: `line` is where it starts."""

    line: int
    text: str


class RepoIndex:
    """Every path in the tree, indexed the two ways the tiers need to ask about it."""

    def __init__(self, root: Path) -> None:
        """Walk `root` once, skipping `SKIP_DIRS`."""
        self.root = root
        self.posix: set[str] = set()
        self.dir_names: set[str] = set()
        for path in root.rglob("*"):
            if not SKIP_DIRS.isdisjoint(path.parts):
                continue

            rel = path.relative_to(root).as_posix()
            self.posix.add(rel)
            if path.is_dir():
                self.dir_names.add(path.name)

    def resolves_from_root(self, token: str) -> bool:
        """True if `token` names a path from the repo root."""
        return token in self.posix

    def resolves_as_suffix(self, token: str) -> bool:
        """True if `token` is a trailing run of segments of some path in the tree."""
        needle = "/" + token
        return any(p == token or p.endswith(needle) for p in self.posix)


def _strip_edges(raw: str) -> str:
    """Trim prose punctuation a path token collects at either end."""
    token = raw.strip("`'\"()[],;:!?")
    while token.endswith((".", "/", "-")):
        token = token[:-1]

    if token.startswith("./"):
        # `./scripts/run_mcp_server.sh` in a shell line is repo-root-relative.
        token = token[2:]

    return token.lstrip("(`'\"")


def candidate_tokens(text: str) -> list[str]:
    """Every path-shaped run in `text`: a token holding at least one `/`.

    Splitting on characters that cannot appear in a path (`:` above all) is what makes
    URLs arrive as absolute paths and get dropped by the caller's absolute-path rule.
    """
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_@./~*-<>{}?+")
    out: list[str] = []
    current: list[str] = []
    for char in text:
        if char in allowed:
            current.append(char)
            continue

        if current:
            out.append("".join(current))
            current = []

    if current:
        out.append("".join(current))

    return [t for t in (_strip_edges(t) for t in out) if "/" in t]


def is_checkable(token: str) -> bool:
    """True if `token` is a claim about this repo that resolution can settle.

    Every `False` here is one of the documented skips: absolute (remote) paths, globs
    and placeholders, vendored or generated trees, and prose that merely contains a
    slash (which is why a known extension is required).
    """
    if token.startswith("/"):
        return False

    if any(marker in token for marker in PLACEHOLDER_CHARS):
        return False

    parts = token.split("/")
    # A leading dot is a module specifier ("./orbPalette.ts") unless the dot belongs to a
    # top-level directory of this repo, which is how `.github/workflows/ci.yml` is cited.
    if token.startswith(".") and parts[0] not in ROOT_ANCHORS:
        return False

    if not SKIP_DIRS.isdisjoint(parts):
        return False

    return token.endswith(SOURCE_EXTS)


def token_resolves(token: str, index: RepoIndex) -> bool:
    """True if `token` points at something in the tree, per its tier.

    Tier 1 (repo-root-anchored) resolves exactly. Tier 2 (a relative shorthand) is
    checked only when its first segment names a directory in this tree, and then passes
    on a path-suffix match.
    """
    first = token.split("/")[0]
    if first in ROOT_ANCHORS:
        return index.resolves_from_root(token)

    if first not in index.dir_names:
        return True  # Not a claim about this tree (another project, an npm package).

    return index.resolves_as_suffix(token)


def markdown_chunks(source: str) -> list[Chunk]:
    """Every line of a Markdown file except the language-tagged code fences."""
    out: list[Chunk] = []
    fence: str | None = None
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            info = stripped[3:].strip().split()
            if fence is None:
                fence = info[0].lower() if info else ""
            else:
                fence = None

            continue

        if fence is not None and fence not in CHECKED_FENCES:
            continue

        out.append(Chunk(number, line))

    return out


def python_chunks(source: str, path: Path) -> list[Chunk]:
    """Every `#` comment and every docstring in one module.

    Ordinary string literals are deliberately absent: this repo's prompts and sandbox
    tests hold plenty of paths that live on the agent's VM, not here.
    """
    out: list[Chunk] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            out.append(Chunk(token.start[0], token.string))

    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue

        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            out.append(Chunk(value.lineno, value.value))

    return out


def js_chunks(source: str) -> list[Chunk]:
    """Every `//` and `/* */` comment in one TS/TSX/CSS file.

    A hand-rolled state machine rather than a regex, so that the `//` inside a quoted
    URL is not read as the start of a comment. Regex literals are treated as code; the
    worst a mis-lex can do is hand back a fragment, which then fails `is_checkable`.
    """
    out: list[Chunk] = []
    state = "code"
    line = 1
    buffer: list[str] = []
    start = 1
    index = 0
    while index < len(source):
        char = source[index]
        pair = source[index : index + 2]
        if char == "\n":
            line += 1

        if state == "code":
            if pair == "//":
                state, start, buffer, index = "line", line, [], index + 2
                continue

            if pair == "/*":
                state, start, buffer, index = "block", line, [], index + 2
                continue

            if char in "'\"`":
                state = {"'": "sq", '"': "dq", "`": "tick"}[char]

            index += 1
            continue

        if state in {"sq", "dq", "tick"}:
            if char == "\\":
                index += 2
                continue

            if char == {"sq": "'", "dq": '"', "tick": "`"}[state]:
                state = "code"

            index += 1
            continue

        if state == "line":
            if char == "\n":
                out.append(Chunk(start, "".join(buffer)))
                state = "code"
                index += 1
                continue

            buffer.append(char)
            index += 1
            continue

        # state == "block"
        if pair == "*/":
            out.append(Chunk(start, "".join(buffer)))
            state, index = "code", index + 2
            continue

        buffer.append(char)
        index += 1

    if state in {"line", "block"}:
        out.append(Chunk(start, "".join(buffer)))

    return out


def chunks_for(path: Path) -> list[Chunk]:
    """The prose worth scanning in one file, chosen by its extension."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        return markdown_chunks(source)

    if path.suffix == ".py":
        return python_chunks(source, path)

    return js_chunks(source)


def default_sources() -> list[Path]:
    """Every file this checker scans by default."""
    out: list[Path] = [ROOT / name for name in MARKDOWN_SOURCES]
    for pattern in MARKDOWN_GLOBS:
        out.extend(sorted(ROOT.glob(pattern)))

    for tree in PYTHON_TREES:
        out.extend(p for p in sorted((ROOT / tree).rglob("*.py")) if SKIP_DIRS.isdisjoint(p.parts))

    for suffix in FRONTEND_EXTS:
        out.extend(
            p
            for p in sorted((ROOT / FRONTEND_TREE).rglob(f"*{suffix}"))
            if SKIP_DIRS.isdisjoint(p.parts)
        )

    return [p for p in out if p.exists()]


def label_for(path: Path) -> str:
    """The name a violation is reported under: repo-relative where that is possible."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        # A file outside the tree, which is how the unit tests feed it samples.
        return path.as_posix()


def violations_in_file(
    path: Path, index: RepoIndex
) -> tuple[list[Violation], set[tuple[str, str]]]:
    """Stale citations in one file, plus the allowlist keys it used."""
    rel = label_for(path)
    found: list[Violation] = []
    used: set[tuple[str, str]] = set()
    for chunk in chunks_for(path):
        for offset, line in enumerate(chunk.text.splitlines() or [""]):
            for token in candidate_tokens(line):
                if not is_checkable(token) or token_resolves(token, index):
                    continue

                if (rel, token) in ALLOWLIST:
                    used.add((rel, token))
                    continue

                found.append(Violation(rel, chunk.line + offset, token))

    # Sorted by line: chunks arrive comments-first, which would read as out of order.
    return sorted(found, key=lambda v: (v.line, v.token)), used


def main(argv: list[str] | None = None) -> int:
    """Check every source, and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "sources",
        nargs="*",
        help="files to scan (default: this repo's Markdown, Python comments, TS comments)",
    )
    args = parser.parse_args(argv)

    if args.sources:
        sources = [Path(s) if Path(s).is_absolute() else ROOT / s for s in args.sources]
    else:
        sources = default_sources()

    index = RepoIndex(ROOT)
    offenders: list[Violation] = []
    used: set[tuple[str, str]] = set()
    for path in sources:
        found, hit = violations_in_file(path, index)
        offenders.extend(found)
        used |= hit

    stale = sorted(set(ALLOWLIST) - used) if not args.sources else []
    if offenders:
        print(f"\nx {len(offenders)} citation(s) of a path that is not in the repo:\n")
        for violation in offenders:
            print(f"  {violation.source}:{violation.line}: {violation.token}")

        print(
            "\nEach one is a comment or a doc sentence pointing at a file that moved or was\n"
            "deleted. Fix the citation to the real path. If it genuinely cannot resolve, add\n"
            "it to ALLOWLIST in scripts/check_doc_paths.py with a reason.\n"
        )

    if stale:
        print(f"\nx {len(stale)} ALLOWLIST entry(ies) in check_doc_paths.py match nothing:\n")
        for source, token in stale:
            print(f"  {source}: {token}")

        print("\nThe citation was fixed or removed. Delete the entry.\n")

    if offenders or stale:
        return 1

    print(f"ok  every cited repo path resolves ({len(sources)} sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
