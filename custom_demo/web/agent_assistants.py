"""Assistant writes for an agent on Managed Deep Agents.

The SPA reads assistants straight from MDA with its token, but MDA refuses assistant
writes from a token caller, so create, update and delete come through here and go on
as the workspace (`agent_client.agent_admin_client`). Only these three operations are
relayed; threads and runs never pass through this deployment.

Off MDA the SPA writes to this deployment's own `/assistants` and never calls these.
"""

from __future__ import annotations

from starlette.responses import JSONResponse, Response

from custom_demo.agent_auth import agent_url
from custom_demo.agent_client import AgentAdminNotConfiguredError, agent_admin_client
from custom_demo.web.errors import err

# What the SPA may set on an assistant; anything else in the body is dropped.
_WRITABLE = ("name", "context", "config", "metadata", "graph_id")


def _not_on_mda() -> JSONResponse | None:
    if agent_url():
        return None

    return err(404, "agent_not_on_mda", "AGENT_URL is not set: write to /assistants here.")


async def _body(request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        return {}

    return {k: body[k] for k in _WRITABLE if isinstance(body, dict) and k in body}


async def _relay(call) -> Response:
    """Run one admin-client call, answering the SPA in the shape `api.ts` reads."""
    try:
        result = await call(agent_admin_client())
    except AgentAdminNotConfiguredError as exc:
        return err(500, "agent_admin_not_configured", str(exc))
    except Exception as exc:  # noqa: BLE001 - route boundary: the SPA shows `error`
        status = getattr(getattr(exc, "response", None), "status_code", 502)
        return err(status, "agent_write_failed", f"{type(exc).__name__}: {exc}")

    return JSONResponse(result) if result is not None else Response(status_code=204)


async def create_agent_assistant(request):
    """POST /agent-assistants: create an assistant on the MDA deployment."""
    if (refusal := _not_on_mda()) is not None:
        return refusal

    body = await _body(request)
    return await _relay(lambda client: client.assistants.create(**body))


async def update_agent_assistant(request):
    """PATCH /agent-assistants/{assistant_id}: update one."""
    if (refusal := _not_on_mda()) is not None:
        return refusal

    body = await _body(request)
    assistant_id = request.path_params["assistant_id"]
    return await _relay(lambda client: client.assistants.update(assistant_id, **body))


async def delete_agent_assistant(request):
    """DELETE /agent-assistants/{assistant_id}: delete one."""
    if (refusal := _not_on_mda()) is not None:
        return refusal

    assistant_id = request.path_params["assistant_id"]
    return await _relay(lambda client: client.assistants.delete(assistant_id))
