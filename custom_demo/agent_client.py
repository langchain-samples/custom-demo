"""SDK clients for the deployment that hosts `dashboard_agent` and its assistants.

With `AGENT_URL` set the agent runs on Managed Deep Agents, so its assistants, threads
and runs live there: `agent_client` targets that URL with a token from `agent_auth`.
Unset, the agent runs on this deployment and the client is the in-process one,
authenticated with the app's shared token exactly as before.

MDA lets a token caller read assistants but never write them: assistants are the
deployment's configuration, not caller data. Writes go through `agent_admin_client`,
which authenticates as a workspace principal (the path LangSmith Studio uses).
"""

from __future__ import annotations

import os
from typing import Any

from langgraph_sdk import get_client

from custom_demo.agent_auth import agent_headers, agent_url


def agent_client() -> Any:
    """The `langgraph_sdk` client for the agent's own deployment."""
    url = agent_url()
    if url:
        return get_client(url=url, headers=agent_headers())

    return get_client(api_key=os.getenv("APP_SHARED_SECRET", "").strip() or None)


class AgentAdminNotConfiguredError(RuntimeError):
    """An assistant write was asked for, but there is no workspace key to make it with."""


def agent_admin_client() -> Any:
    """A client allowed to create, update and delete assistants on the agent's deployment.

    On MDA that is a workspace principal: `x-auth-scheme: langsmith` with a LangSmith key
    for the workspace hosting the deployment. Off MDA it is `agent_client`.
    """
    url = agent_url()
    if not url:
        return agent_client()

    key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if not key:
        raise AgentAdminNotConfiguredError(
            "LANGSMITH_API_KEY is not set, so assistants on the MDA deployment cannot be "
            "created or changed (MDA allows assistant writes only from the workspace)."
        )

    return get_client(url=url, api_key=key, headers={"x-auth-scheme": "langsmith"})
