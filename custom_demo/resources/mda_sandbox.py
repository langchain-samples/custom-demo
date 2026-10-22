"""Find a Managed Deep Agents thread's sandbox from outside the MDA deployment.

MDA gives each thread its own sandbox and names it deterministically from the
deployment name and the thread id, so the file routes on the LangSmith deployment can
attach to the same box a chat turn uses without asking MDA. The name comes from MDA's
own (private) function rather than a copy of its rule; `test_mda_sandbox.py` pins the
rule, so an SDK release that changes it fails a test instead of emptying the Files panel.

The plain per-thread name only holds while the agent factory leaves `sandbox` unset for
the run (root `agent.py`): a factory-selected sandbox adds a selection fingerprint.
"""

from __future__ import annotations

import os
from typing import Any

from managed_deepagents.runtime import _managed_sandbox_name

from custom_demo.resources.sandbox import attach_named_sandbox

# `mda deploy` names the deployment after the agent unless `--name` overrides it.
DEFAULT_DEPLOYMENT_NAME = "dashboard-agent"


def deployment_name() -> str:
    """The MDA deployment's name, which prefixes every sandbox it creates."""
    return os.getenv("AGENT_DEPLOYMENT_NAME", "").strip() or DEFAULT_DEPLOYMENT_NAME


def thread_sandbox_name(thread_id: str) -> str:
    """The name MDA gives `thread_id`'s sandbox."""
    return _managed_sandbox_name(deployment_name(), f"thread:{thread_id}")


def attach_thread_sandbox(thread_id: str) -> Any | None:
    """Attach to a thread's sandbox if a run has created it; never create one."""
    return attach_named_sandbox(thread_sandbox_name(thread_id))
