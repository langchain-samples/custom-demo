"""The two error shapes the custom routes answer with.

`err` is the sandbox routes' richer body: a human `error` next to a `reason` slug
the SPA branches on. `route_error` is the plain 500 that nine routes each spelled
out for themselves — as a decorator it also lifts the whole handler out of a
`try:` block, which is what lets the routes below read as a list of guard clauses
instead of one long indented arm.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

Handler = Callable[[Any], Awaitable[JSONResponse]]


def err(status: int, reason: str, message: str) -> JSONResponse:
    """Error body carrying both a human `error` (what api.ts reads) and a `reason` slug."""
    return JSONResponse({"error": message, "reason": reason}, status_code=status)


def route_error(handler: Handler) -> Handler:
    """Turn an unexpected failure in `handler` into a 500 body, never a rendered stack.

    The SPA reads `error` off every failed call, so a route that raised would show
    the presenter a traceback where a sentence belongs. Applied only where the
    whole handler wants that one answer; a route with its own degraded mode (a 502
    for a mint that failed, a 200 for a span that was lost) keeps its own handler.
    """

    @functools.wraps(handler)
    async def guarded(request):
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001 - route boundary: report the failure to the SPA as a 500 body
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return guarded
