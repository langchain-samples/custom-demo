"""Managed Deep Agents sandbox: one VM per thread, cloned from the `setup.sh` snapshot.

The root `agent.py` factory leaves this selection unset for an assistant that wants a
sandbox and passes `None` for one that does not. The idle TTL matches the LangSmith
deployment's assistant VMs (`custom_demo/resources/sandbox.py`).
"""

from managed_deepagents import define_sandbox

sandbox = define_sandbox(
    idle_ttl_seconds=3600,
    default_timeout=600,
)
