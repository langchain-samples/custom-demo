#!/usr/bin/env python3
"""Run this BEFORE the session. It tells you exactly what's missing.

    uv run python scripts/preflight.py

Checks, cheapest first, and keeps going after a failure so you get the whole picture
in one pass rather than fixing one thing, re-running, and finding the next:

    1. Python version     >= 3.13
    2. Toolchain          uv, node, npm (the SPA needs the last two)
    3. Imports            the packages this repo actually imports
    4. Environment        .env found, and WHICH model provider it selects
    5. Model provider     ONE cheap real call, against whatever provider you chose
    6. LangSmith          auth, and the workspace the demo will write to
    7. Prompt Hub         the system prompt is seeded and readable
    8. Agent Server       the langgraph CLI the run script needs

Exit code 0 means you are ready. Anything else prints the fix.

Every check is written to fail with an instruction, not a stack trace: the point is
that someone who has never seen this repo can act on the output without reading it.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_FAILED: list[str] = []
_WARNED: list[str] = []


def ok(msg: str) -> None:
    """Report a passing check."""
    print(f"  \033[32mok\033[0m    {msg}")


def warn(label: str, msg: str) -> None:
    """Report a non-fatal problem: setup works, but something is degraded."""
    _WARNED.append(label)
    print(f"  \033[33mwarn\033[0m  {msg}")


def fail(label: str, msg: str, fix: str) -> None:
    """Report a blocking problem, followed by the exact command that fixes it."""
    _FAILED.append(label)
    print(f"  \033[31mFAIL\033[0m  {msg}")
    for line in fix.strip().splitlines():
        print(f"        {line}")


def head(n: int, title: str) -> None:
    """Print a numbered section header."""
    print(f"\n{n}. {title}")


# --- 1. python -----------------------------------------------------------------


def check_python() -> None:
    """Check the interpreter is new enough for this project."""
    head(1, "Python version")
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 13):
        fail(
            "python",
            f"Python {major}.{minor}, but this project needs >= 3.13.",
            "uv provisions its own Python, so the usual fix is to run through uv:\n"
            "  uv sync --group dev && uv run python scripts/preflight.py",
        )
        return
    ok(f"Python {major}.{minor}")


# --- 2. toolchain --------------------------------------------------------------


def check_toolchain() -> None:
    """Check for uv, node and npm — none of which this repo can install itself."""
    head(2, "Toolchain")
    if shutil.which("uv"):
        ok("uv")
    else:
        fail(
            "uv",
            "uv is not installed.",
            "curl -LsSf https://astral.sh/uv/install.sh | sh\n"
            "(or: brew install uv / pipx install uv)",
        )
    # The SPA is a Vite app; run.sh will npm-install it on first start.
    for tool in ("node", "npm"):
        if shutil.which(tool):
            ok(tool)
        else:
            fail(
                tool,
                f"{tool} is not installed, so the dashboard UI cannot start.",
                "Install Node 20+: https://nodejs.org (or: brew install node)\n"
                "The agent and its evals still work without it; only the SPA needs this.",
            )


# --- 3. imports ----------------------------------------------------------------


def check_imports() -> None:
    """Check the packages this repo imports are actually installed."""
    head(3, "Imports")
    required = ["langchain", "langgraph", "deepagents", "langsmith", "dotenv", "starlette"]
    missing = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        fail(
            "imports",
            f"Missing packages: {', '.join(missing)}",
            "uv sync --group dev",
        )
        return
    ok(f"{len(required)} core packages")


# --- 4. environment ------------------------------------------------------------


def _provider() -> str:
    from dashboard_agent.config import MODEL, model_provider

    return model_provider(os.getenv("DASHBOARD_MODEL") or MODEL)


def check_env() -> str:
    """Load .env and report which model provider it selects. Returns the provider."""
    head(4, "Environment")
    from dashboard_agent.config import load_env

    if not (ROOT / ".env").exists():
        warn("env", "No .env file. Relying on variables already in your shell.")
    load_env()

    model_id = os.getenv("DASHBOARD_MODEL") or "claude-sonnet-5"
    provider = _provider()
    ok(f".env loaded — model '{model_id}' (provider: {provider})")

    # An Anthropic base-url override is easy to inherit from another tool's shell and
    # silently redirects every call, which makes results untraceable to the model you
    # think you are testing. Worth surfacing rather than debugging later.
    if provider == "anthropic" and os.getenv("ANTHROPIC_BASE_URL"):
        warn(
            "gateway",
            f"ANTHROPIC_BASE_URL is set ({os.environ['ANTHROPIC_BASE_URL']}). "
            "Model calls are going through that proxy, not to Anthropic directly.",
        )
    return provider


# --- 5. model ------------------------------------------------------------------


def check_model(provider: str) -> None:
    """Make one cheap real call against whichever provider is configured."""
    head(5, "Model provider (one real call)")
    from dashboard_agent.config import MODEL, require_model_key

    model_id = os.getenv("DASHBOARD_MODEL") or MODEL
    try:
        require_model_key(model_id)
    except RuntimeError as exc:
        fix = "Add it to .env, then re-run."
        if provider == "azure_openai":
            fix = (
                "An Azure OpenAI deployment needs all four:\n"
                "  DASHBOARD_MODEL=azure_openai:<deployment>\n"
                "  AZURE_OPENAI_ENDPOINT=https://<host>/<base>   (NOT including /openai/...)\n"
                "  AZURE_OPENAI_API_KEY=...\n"
                "  OPENAI_API_VERSION=2024-12-01-preview"
            )
        fail("model-key", str(exc), fix)
        return

    if provider == "azure_openai":
        for var in ("AZURE_OPENAI_ENDPOINT", "OPENAI_API_VERSION"):
            if not os.getenv(var):
                fail(
                    "model-key",
                    f"{var} is not set, and it is required for an Azure deployment.",
                    "See .env.example, section 'Non-Anthropic model providers'.",
                )
                return

    try:
        from dashboard_agent.agent import build_chat_model

        llm = build_chat_model(model_id)
        reply = llm.invoke("Reply with exactly: OK")
        text = getattr(reply, "content", "")
        ok(f"{model_id} answered ({str(text)[:40]!r})")
    except Exception as exc:  # noqa: BLE001 - any failure here is a setup problem
        detail = f"{type(exc).__name__}: {exc}"
        hint = "Check the key and, for Azure, that the endpoint excludes /openai/deployments/..."
        if "temperature" in detail.lower():
            hint = (
                "This model rejects the temperature we send. Set DASHBOARD_TEMPERATURE=\n"
                "(empty) in .env to omit the parameter entirely."
            )
        fail("model", f"Call failed. {detail[:220]}", hint)


# --- 6. langsmith --------------------------------------------------------------


def check_langsmith() -> None:
    """Check the LangSmith key authenticates, and name the workspace it lands in."""
    head(6, "LangSmith")
    if not os.getenv("LANGSMITH_API_KEY"):
        fail(
            "langsmith",
            "LANGSMITH_API_KEY is not set.",
            "Get one at https://smith.langchain.com (Settings -> API Keys), add to .env.\n"
            "Tracing, Prompt Hub and the demo evals all need it.",
        )
        return
    try:
        from dashboard_agent.config import make_client, workspace_id

        client = make_client()
        list(client.list_projects(limit=1))
        ok(f"authenticated (workspace: {workspace_id() or 'default for this key'})")
    except Exception as exc:  # noqa: BLE001
        fail(
            "langsmith",
            f"Auth failed. {type(exc).__name__}: {str(exc)[:180]}",
            "Check LANGSMITH_API_KEY, and WORKSPACE_ID if you set one.",
        )


# --- 7. prompt hub -------------------------------------------------------------


def check_prompt_hub() -> None:
    """Check the system prompt is seeded, since the live-fix demo depends on it."""
    head(7, "Prompt Hub")
    if not os.getenv("LANGSMITH_API_KEY"):
        warn("hub", "Skipped: no LangSmith key.")
        return
    try:
        from dashboard_agent.config import prompt_name
        from dashboard_agent.prompt import pull_system_prompt

        text = pull_system_prompt(None)
        if not text:
            raise RuntimeError("empty prompt")
        ok(f"'{prompt_name()}' readable ({len(text)} chars)")
    except Exception as exc:  # noqa: BLE001
        # Not fatal: the code falls back to a bundled prompt. But the live "fix the
        # bug in the Hub and re-run" demo is exactly what stops working.
        warn(
            "hub",
            f"Could not pull the system prompt ({type(exc).__name__}). "
            "Seed it with: uv run python scripts/seed_prompt.py",
        )


# --- 8. agent server -----------------------------------------------------------


def check_agent_server() -> None:
    """Check the langgraph CLI that run.sh needs is present."""
    head(8, "Agent Server")
    try:
        proc = subprocess.run(  # noqa: S603
            ["uv", "run", "langgraph", "--version"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        fail("langgraph", f"Could not run the langgraph CLI ({exc}).", "uv sync --group dev")
        return
    if proc.returncode != 0:
        fail(
            "langgraph",
            "The langgraph CLI is not available.",
            "uv sync --group dev    # the dev group provides langgraph-cli[inmem]",
        )
        return
    ok((proc.stdout or proc.stderr).strip().splitlines()[0][:60])


def main() -> int:
    """Run every check, then summarise. Returns a process exit code."""
    print("Dashboard Agent preflight")
    check_python()
    check_toolchain()
    check_imports()
    if _FAILED:
        # Everything below imports the package, which cannot work yet.
        print("\nFix the above first, then re-run: the later checks import this project.")
        return 1
    provider = check_env()
    check_model(provider)
    check_langsmith()
    check_prompt_hub()
    check_agent_server()

    print()
    if _FAILED:
        print(f"\033[31m{len(_FAILED)} check(s) failed:\033[0m {', '.join(_FAILED)}")
        return 1
    if _WARNED:
        print(f"\033[33mALL CHECKS PASSED\033[0m, with warnings: {', '.join(_WARNED)}")
        return 0
    print("\033[32mALL CHECKS PASSED\033[0m — you are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
