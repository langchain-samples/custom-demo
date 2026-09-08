"""Tool mocking. No network, no API key, no model.

The case that motivates all of this: `web_search` returning nothing. The demo's
honesty check depends on that precondition, and without mocking it depends on
what Tavily happens to answer — a live third party inside the system under test.
Mocking pins it. These tests pin the mocking.

The heading is a constraint, not a description: a test here that reaches the
network passes on a laptop with keys and fails in CI, which is exactly how the
two pass-through cases below broke.
"""

from __future__ import annotations

import json

import pytest
from langchain.tools import tool as tool_decorator

from custom_demo.runtime.mocking import (
    MockedToolError,
    active_mocks,
    enable_mocking,
    install_mocks,
    restore_mocks,
    using_mocks,
)
from custom_demo.runtime.tools.web_search import web_search


@pytest.fixture
def tool():
    """A mockable clone of the real web_search tool."""
    (clone,) = enable_mocking([web_search])
    return clone


@pytest.fixture
def passthrough():
    """A mockable clone of a tool whose real implementation is observable.

    Every mock here is keyed by tool NAME, so a stand-in exercises the same
    lookup the catalogue does. Used only by the two tests that let the call
    reach the real function.
    """

    @tool_decorator
    def web_search(query: str) -> str:
        """Same name as the catalogue tool, so the same spec key applies."""
        return f"real: {query}"

    (clone,) = enable_mocking([web_search])
    return clone


def call(tool, query: str = "anything") -> str:
    """Invoke through `.func`, the seam the tool machinery calls.

    `.invoke()` would go through the tool machinery, which is irrelevant to
    whether a mock intercepted the call.
    """
    return tool.func(query=query)


# --- the contract is preserved --------------------------------------------------


def test_mock_keeps_the_real_tools_contract(tool):
    """Name, description and args are what the model reasons about.

    A mock that redeclares them tests a tool the production agent does not have,
    and passes where the real one would fail.
    """
    assert tool.name == web_search.name
    assert tool.description == web_search.description
    assert tool.args == web_search.args


def test_mocking_does_not_mutate_the_original_tool(tool):
    """Importing this module must not change how the deployed agent behaves."""
    assert tool is not web_search
    # `func` lives on StructuredTool, which is what these are at runtime; the
    # annotation upstream is the base BaseTool, so read it off dynamically.
    assert getattr(tool, "func", None) is not getattr(web_search, "func", None)


# --- the two cases asked for ----------------------------------------------------


def test_web_search_can_return_empty(tool):
    with using_mocks({"web_search": {"results": []}}):
        assert json.loads(call(tool)) == {"results": []}


def test_web_search_can_return_results(tool):
    world = {"results": [{"title": "Q2 report", "data": [{"label": "Apr", "value": 1}]}]}
    with using_mocks({"web_search": world}):
        assert json.loads(call(tool)) == world


# --- conventions ----------------------------------------------------------------


def test_a_string_mock_is_returned_verbatim(tool):
    with using_mocks({"web_search": "raw passthrough"}):
        assert call(tool) == "raw passthrough"


def test_a_list_mock_is_a_sequence_of_responses(tool):
    with using_mocks({"web_search": [{"results": ["first"]}, {"results": ["second"]}]}):
        assert json.loads(call(tool))["results"] == ["first"]
        assert json.loads(call(tool))["results"] == ["second"]


def test_a_sequence_clamps_rather_than_running_out(tool):
    """An agent that retries once more shouldn't fail on a harness detail."""
    with using_mocks({"web_search": [{"results": ["only"]}]}):
        call(tool)
        assert json.loads(call(tool))["results"] == ["only"]


def test_a_nested_list_returns_that_list_every_time(tool):
    """The sharp edge of the sequence rule: a tool whose value IS a list needs nesting."""
    with using_mocks({"web_search": [["a", "b"]]}):
        assert json.loads(call(tool)) == ["a", "b"]
        assert json.loads(call(tool)) == ["a", "b"]


def test_an_empty_list_is_an_empty_result_not_an_empty_sequence(tool):
    with using_mocks({"web_search": []}):
        assert json.loads(call(tool)) == []


def test_raise_is_the_only_reserved_word(tool):
    with using_mocks({"web_search": {"raise": "503 Service Unavailable"}}):
        with pytest.raises(MockedToolError, match="503"):
            call(tool)


def test_an_error_payload_is_data_not_an_exception(tool):
    """A tool handing back `{"error": ...}` is returning something the model can read.

    Only `raise` takes the throwing path, because that is the case where the model
    never sees a result at all.
    """
    with using_mocks({"web_search": {"error": "503 Service Unavailable"}}):
        assert json.loads(call(tool)) == {"error": "503 Service Unavailable"}


# --- scoping --------------------------------------------------------------------


def test_no_spec_means_the_real_tool_runs(passthrough):
    """With nothing installed the wrapper calls straight through.

    Asserted against a local tool rather than `web_search`, whose real
    implementation is a Tavily HTTP call: reaching the network to prove the
    wrapper is inert makes the test need a key and an outbound connection, which
    is how this file ended up passing locally and failing in CI.
    """
    assert active_mocks() is None
    assert call(passthrough, "funding") == "real: funding"


def test_an_unlisted_tool_is_not_mocked(passthrough):
    """A spec for another tool must not silently capture this one."""
    with using_mocks({"some_other_tool": "x"}):
        assert active_mocks() == {"some_other_tool": "x"}
        assert call(passthrough, "funding") == "real: funding"


def test_mocks_do_not_leak_past_the_block(tool):
    with using_mocks({"web_search": {"results": []}}):
        pass

    assert active_mocks() is None


def test_token_form_matches_the_context_manager(tool):
    token = install_mocks({"web_search": "via token"})
    try:
        assert call(tool) == "via token"
    finally:
        restore_mocks(token)

    assert active_mocks() is None


def test_call_counts_reset_between_specs(tool):
    """Two examples in one experiment must not share a sequence position."""
    with using_mocks({"web_search": [{"n": 1}, {"n": 2}]}):
        assert json.loads(call(tool))["n"] == 1

    with using_mocks({"web_search": [{"n": 1}, {"n": 2}]}):
        assert json.loads(call(tool))["n"] == 1
