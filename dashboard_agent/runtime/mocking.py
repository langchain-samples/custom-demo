"""Mock what a tool returns, per invocation, from a dataset row.

Why this exists. The demo's honesty test — does the assistant admit it has no data
instead of inventing figures — currently depends on the *synthetic data source*
choosing not to return anything for the gap topic. That is an LLM decision inside
the thing under test, so the test's precondition is itself stochastic: a run that
"passes" may have passed because the data source happened to return nothing, and a
run that fails may have failed because it happened to return something. Mocking the
tool makes the precondition deterministic. The agent is still the thing being
graded; the world it is graded against stops moving.

    "mock_tools": {
        "datasearch": {"results": []},              # the empty case
        "create_ticket": {"raise": "503 Service Unavailable"},
    }

Conventions, matching the eval material so one set of rules covers both:

| mock value        | behavior                                            |
| :---------------- | :-------------------------------------------------- |
| any value         | returned as the tool result, error payloads included |
| `{"raise": "..."}`| throws instead of returning                          |
| `[a, b, ...]`     | successive calls get successive responses            |

`raise` is the only reserved word. Everything else is handed to the model as-is,
which includes an error payload: a tool returning `{"error": "503"}` is returning
data the model can read and recover from. Throwing is the case that needs its own
form, because it takes a different code path and the model never sees a result.

That last row has a sharp edge: a bare list means *sequence*, so a tool whose real
return value is a list needs nesting — `[[a, b]]` — to mean "return this list every
time".

Unmocked tools are untouched and call straight through, so wrapping the catalogue
is a no-op for every existing assistant.
"""

from __future__ import annotations

import contextvars
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

# Mocks for the invocation in flight. A ContextVar rather than a build-time argument
# because the eval target builds the agent ONCE and then runs every example through
# it — per-example mocks have to travel with the call, not the graph. Same pattern as
# `tools.widget_sink`.
_active: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mock_tools", default=None
)

# Call counts for sequence mocks, reset whenever a new spec is installed.
_calls: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "mock_tool_calls", default=None
)


class MockedToolError(RuntimeError):
    """Raised by a tool whose mock is `{"raise": ...}`.

    A distinct type so a test can tell "the mock fired as designed" apart from "the
    tool genuinely broke".
    """


def install_mocks(spec: dict[str, Any] | None) -> tuple[Any, Any]:
    """Install `spec` and return a token to pass to `restore_mocks`.

    The token form exists for callers whose try/finally already wraps a long block
    (the eval target does exactly this for its widget sink); `using_mocks` is the
    context-manager sugar over it.
    """
    return _active.set(spec or None), _calls.set({})


def restore_mocks(token: tuple[Any, Any]) -> None:
    """Undo an `install_mocks`."""
    active_token, calls_token = token
    _active.reset(active_token)
    _calls.reset(calls_token)


@contextmanager
def using_mocks(spec: dict[str, Any] | None) -> Iterator[None]:
    """Install `spec` for the duration of the block. `None` disables mocking."""
    token = install_mocks(spec)
    try:
        yield
    finally:
        restore_mocks(token)


def active_mocks() -> dict[str, Any] | None:
    """The spec in force right now, if any."""
    return _active.get()


def _render(value: Any) -> str:
    """Tool results reach the model as text; JSON-encode anything that isn't already."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _resolve(name: str, spec: Any) -> str:
    """Turn one mock value into the string the tool should return.

    Raises `MockedToolError` for the `raise` form.
    """
    if isinstance(spec, list):
        if not spec:
            # An empty list is a legitimate empty result, not an exhausted sequence.
            return _render(spec)
        counts = _calls.get()
        if counts is None:
            counts = {}
            _calls.set(counts)
        i = counts.get(name, 0)
        counts[name] = i + 1
        # Past the end, keep returning the last entry rather than raising: an agent
        # that retries one extra time should not fail on a harness detail.
        spec = spec[min(i, len(spec) - 1)]

    if isinstance(spec, dict) and set(spec) == {"raise"}:
        raise MockedToolError(str(spec["raise"]))

    return _render(spec)


def mock_tool(real: BaseTool) -> BaseTool:
    """Return a copy of `real` that consults the active mock spec at call time.

    The copy keeps the real tool's **name, description and args schema**. Those are
    what the model sees and reasons about, so cloning them rather than redeclaring
    them is what stops a mock from quietly testing a different tool than production
    has. A mock whose contract has drifted from the real one passes tests the real
    tool would fail, which is worse than having no test.

    A copy, not an in-place patch, so importing this module can never change how the
    deployed agent behaves.
    """
    if not isinstance(real, StructuredTool):
        # Nothing else in the catalogue is built this way today; be loud rather than
        # silently handing back an unmockable tool that reports as mocked.
        raise TypeError(f"cannot mock {real.name!r}: expected StructuredTool, got {type(real)}")

    inner_func, inner_coro = real.func, real.coroutine

    def _mocked(*args: Any, **kwargs: Any) -> Any:
        spec = _active.get()
        if spec is not None and real.name in spec:
            return _resolve(real.name, spec[real.name])
        if inner_func is None:
            raise TypeError(f"{real.name!r} has no sync implementation")
        return inner_func(*args, **kwargs)

    async def _mocked_async(*args: Any, **kwargs: Any) -> Any:
        spec = _active.get()
        if spec is not None and real.name in spec:
            return _resolve(real.name, spec[real.name])
        if inner_coro is not None:
            return await inner_coro(*args, **kwargs)
        if inner_func is None:
            raise TypeError(f"{real.name!r} has no implementation")
        return inner_func(*args, **kwargs)

    clone = real.model_copy()
    object.__setattr__(clone, "func", _mocked if inner_func is not None else None)
    object.__setattr__(clone, "coroutine", _mocked_async)
    return clone


def enable_mocking(tools: list[BaseTool]) -> list[BaseTool]:
    """Make every tool mockable. Inert until `using_mocks` installs a spec.

    Safe to apply unconditionally: with no spec active every call falls straight
    through to the real implementation, so the deployed agent is unaffected.
    """
    return [mock_tool(t) for t in tools]
