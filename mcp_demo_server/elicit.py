"""The guard-pattern elicitation every interactive tool on the demo server uses.

On the modern stateless MCP spec there is no session for a server to push a
question down, so `ctx.elicit()` fails outright ("elicitation via server-initiated
requests is unavailable"). Asking is instead an ordinary result: a tool returns
an `InputRequiredResult` naming what it needs, the client re-calls the same tool
with `input_responses` attached, and `ctx.input_responses` tells the body which
round it is in (SEP-2322).

That retry-able shape is precisely why `langchain.mcp` can surface the pause as a
LangGraph `interrupt()` — a round survives being suspended, an open socket would
not.

**A guard tool re-runs from the top on every round.** Do any real work only after
`answer_for` returns something; anything above that line happens once per round.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Context
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)


def ask(key: str, message: str, schema: dict[str, Any]) -> InputRequiredResult:
    """One round of asking, as the result of this leg of the call.

    Args:
        key: How the answer comes back in `ctx.input_responses`, and what the
            host resumes against. It has to stay stable across rounds.
        message: The prompt a person reads.
        schema: JSON schema the answer must satisfy. Passed to the host
            verbatim, and restricted: the SDK normalizes it to the elicitation
            form, which allows primitives only.
    """
    return InputRequiredResult(
        input_requests={
            key: ElicitRequest(
                params=ElicitRequestFormParams(message=message, requested_schema=schema)
            )
        }
    )


def answer_for(ctx: Context, key: str) -> ElicitResult | None:
    """The client's answer to `key`, or None on the round where nothing was asked yet."""
    responses = ctx.input_responses
    if not responses:
        return None

    answer = responses.get(key)
    return answer if isinstance(answer, ElicitResult) else None
