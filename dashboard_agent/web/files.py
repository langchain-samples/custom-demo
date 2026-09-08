"""Names, paths and text pages for the sandbox browser — the part with no VM in it.

Pure functions over strings, kept apart from `web/sandbox.py` so the security-relevant
decisions (what may be read at all, where an upload may land, how a page is cut) can be
read in one screen without the VM plumbing around them.

Public rather than underscore-private because `web/sandbox.py` calls all of them.
"""

from __future__ import annotations

import posixpath

from dashboard_agent.config import sandbox_files_root

# Our own byte cap on a preview page, on top of the VM's ~500 KiB stdout cap — the
# browser is rendering this into a DOM, not grepping it.
_MAX_CONTENT_BYTES = 256 * 1024

# Extensions we are willing to send through `aread`. This is a SAFETY gate, not a UI
# nicety: deepagents' `_get_file_type` defaults unknown extensions to "text", and the
# in-VM script's text branch falls back to base64-ing the WHOLE file with no size
# guard when the UTF-8 sniff fails. Allowlisting keeps a core dump off that path.
_TEXT_EXTS = frozenset(
    """md markdown mdx txt log csv tsv json jsonl ndjson yaml yml toml ini cfg conf
    py pyi ipynb js jsx ts tsx sh bash sql html htm css scss xml svg rst r rb go rs
    java c h cpp hpp tf mk lock""".split()
)

# Extensionless files worth previewing (the agent writes several of these).
_TEXT_NAMES = frozenset(
    {"dockerfile", "makefile", "readme", "license", "changelog", "agents", "skill"}
)

_MARKDOWN_EXTS = frozenset({"md", "markdown", "mdx"})

# Binaries the BROWSER can render natively, so they are worth shipping as bytes rather
# than refusing. Everything else stays "binary" and gets the placeholder.
MEDIA_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# deepagents' in-VM read script emits this literal for a zero-byte file; it would
# render as body text in the viewer.
EMPTY_FILE_REMINDER = "System reminder: File exists but has empty contents"

# Appended in-VM when the read page blew the sandbox stdout cap (deepagents'
# TRUNCATION_MSG). Matched as a substring so we don't import sandbox internals.
_VM_TRUNCATION_MARKER = "[Output was truncated due to size limits."


def files_root() -> str:
    """Root the browser is confined to (`SANDBOX_FILES_ROOT`, default `/workspace`).

    Configurable so the surface can be tightened without a code change. `config`
    resolves the name (and the deprecated `DA_FILES_ROOT` behind it, and `load_env`);
    confining the result stays here, because a relative root is a route-layer problem.
    """
    root = posixpath.normpath(sandbox_files_root())
    return root if root.startswith("/") else "/workspace"


def safe_path(raw: str, root: str) -> str | None:
    """Normalise an absolute path and confine it to `root`, else None.

    `normpath` resolves `..` BEFORE the prefix test, so `/workspace/../etc` is
    rejected. It cannot see symlinks inside the VM — accepted, since the agent can
    already `execute` arbitrary shell there; this only adds a surface, not a power.
    """
    if not raw or "\x00" in raw or not raw.startswith("/"):
        return None

    path = posixpath.normpath(raw)
    if path != root and not path.startswith(root.rstrip("/") + "/"):
        return None

    return path


def file_kind(name: str) -> str:
    """Classify a file name: "text", "media" (browser-renderable bytes) or "binary"."""
    low = name.lower()
    # Dotenv files are excluded deliberately: the deployment's only auth is a shared
    # token that ships in the SPA bundle, so a browser-reachable `cat` of any secrets
    # the agent wrote into its VM is a needless key-leak path.
    if low.startswith(".env"):
        return "binary"

    ext = posixpath.splitext(low)[1].lstrip(".")
    if not ext:
        return "text" if low in _TEXT_NAMES else "binary"

    if ext in _TEXT_EXTS:
        return "text"

    return "media" if ext in MEDIA_MIME else "binary"


def extension(name: str) -> str:
    """The bare lowercase extension of `name`, "" when it has none."""
    return posixpath.splitext(name.lower())[1].lstrip(".")


def language_of(name: str) -> str | None:
    """Viewer hint: "markdown" for md-family files, else the bare extension."""
    ext = extension(name)
    if ext in _MARKDOWN_EXTS:
        return "markdown"

    return ext or None


def upload_name(raw: str) -> str | None:
    """A safe basename for an uploaded file, or None to reject it.

    Basename only — an upload may choose its NAME, never its directory, so a crafted
    "../../etc/passwd" or an absolute path cannot escape `_UPLOAD_DIR`. Dotenv names are
    refused for the same reason `file_kind` refuses to preview them: the browser next
    door would happily serve one back.
    """
    name = posixpath.basename((raw or "").strip().replace("\\", "/"))
    if not name or name in {".", ".."} or "\x00" in name or name.lower().startswith(".env"):
        return None

    return name


def clip_page(content: str, limit: int) -> tuple[str, int, bool]:
    """One page of file text as `(content, whole_lines, truncated)`.

    Three independent caps can cut a page short, and each one has to be reported — a
    silently-clipped page reads as a complete file. `partial_tail` tracks whether the
    LAST line survived whole, because a half line must be re-fetched from the start on
    the next page rather than skipped; `whole_lines` is what the caller advances by.
    """
    truncated = False
    partial_tail = False
    if _VM_TRUNCATION_MARKER in content:
        # The VM's stdout cap fired. Drop deepagents' operator-facing suffix: the
        # viewer states this in its own words and offers the next page.
        # rstrip: deepagents' TRUNCATION_MSG opens with "\n\n", which is framing
        # for the message, not two blank lines of the file.
        content = content.split(_VM_TRUNCATION_MARKER, 1)[0].rstrip("\n")
        truncated = partial_tail = True

    lines = content.split("\n") if content else []
    if len(lines) > limit:
        # The `limit + 1`-th line came back, so the file continues past this page.
        lines = lines[:limit]
        content = "\n".join(lines)
        truncated, partial_tail = True, False

    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_CONTENT_BYTES:
        content = encoded[:_MAX_CONTENT_BYTES].decode("utf-8", errors="ignore")
        lines = content.split("\n")
        truncated = partial_tail = True

    whole_lines = max(0, len(lines) - 1) if partial_tail else len(lines)
    return content, whole_lines, truncated
