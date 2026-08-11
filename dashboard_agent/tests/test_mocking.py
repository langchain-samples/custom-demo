"""Tool mocking. No network, no API key, no model.

The case that motivates all of this: `datasearch` returning nothing. The demo's
honesty check depends on that precondition, and without mocking it is produced by
the synthetic data source deciding not to return anything — an LLM decision inside
the system under test. These pin the deterministic replacement.
"""

from __future__ import annotations

import json

import pytest

from dashboard_agent.mocking import (
    MockedToolError,
    active_mocks,
    enable_mocking,
    install_mocks,
    restore_mocks,
    using_mocks,
)
from dashboard_agent.tools.core import datasearch


@pytest.fixture
def tool():
    """A mockable clone of the real datasearch tool."""
    (clone,) = enable_mocking([datasearch])
    return clone


def call(tool, query: str = "anything") -> str:
    """Invoke through `.func`, the seam the tool machinery calls.

    `.invoke()` would demand the injected ToolRuntime that the agent supplies at
    run time, which is irrelevant to whether a mock intercepted the call.
    """
    return tool.func(query=query, runtime=None)


# --- the contract is preserved --------------------------------------------------


def test_mock_keeps_the_real_tools_contract(tool):
    """Name, description and args are what the model reasons about.

    A mock that redeclares them tests a tool the production agent does not have,
    and passes where the real one would fail.
    """
    assert tool.name == datasearch.name
    assert tool.description == datasearch.description
    assert tool.args == datasearch.args


def test_mocking_does_not_mutate_the_original_tool(tool):
    """Importing this module must not change how the deployed agent behaves."""
    assert tool is not datasearch
    # `func` lives on StructuredTool, which is what these are at runtime; the
    # annotation upstream is the base BaseTool, so read it off dynamically.
    assert getattr(tool, "func", None) is not getattr(datasearch, "func", None)


# --- the two cases asked for ----------------------------------------------------


def test_datasearch_can_return_empty(tool):
    with using_mocks({"datasearch": {"results": []}}):
        assert json.loads(call(tool)) == {"results": []}


def test_datasearch_can_return_results(tool):
    world = {"results": [{"title": "Q2 report", "data": [{"label": "Apr", "value": 1}]}]}
    with using_mocks({"datasearch": world}):
        assert json.loads(call(tool)) == world


# --- conventions ----------------------------------------------------------------


def test_a_string_mock_is_returned_verbatim(tool):
    with using_mocks({"datasearch": "raw passthrough"}):
        assert call(tool) == "raw passthrough"


def test_a_list_mock_is_a_sequence_of_responses(tool):
    with using_mocks({"datasearch": [{"results": ["first"]}, {"results": ["second"]}]}):
        assert json.loads(call(tool))["results"] == ["first"]
        assert json.loads(call(tool))["results"] == ["second"]


def test_a_sequence_clamps_rather_than_running_out(tool):
    """An agent that retries once more shouldn't fail on a harness detail."""
    with using_mocks({"datasearch": [{"results": ["only"]}]}):
        call(tool)
        assert json.loads(call(tool))["results"] == ["only"]


def test_a_nested_list_returns_that_list_every_time(tool):
    """The sharp edge of the sequence rule: a tool whose value IS a list needs nesting."""
    with using_mocks({"datasearch": [["a", "b"]]}):
        assert json.loads(call(tool)) == ["a", "b"]
        assert json.loads(call(tool)) == ["a", "b"]


def test_an_empty_list_is_an_empty_result_not_an_empty_sequence(tool):
    with using_mocks({"datasearch": []}):
        assert json.loads(call(tool)) == []


def test_raise_is_the_only_reserved_word(tool):
    with using_mocks({"datasearch": {"raise": "503 Service Unavailable"}}):
        with pytest.raises(MockedToolError, match="503"):
            call(tool)


def test_an_error_payload_is_data_not_an_exception(tool):
    """A tool handing back `{"error": ...}` is returning something the model can read.

    Only `raise` takes the throwing path, because that is the case where the model
    never sees a result at all.
    """
    with using_mocks({"datasearch": {"error": "503 Service Unavailable"}}):
        assert json.loads(call(tool)) == {"error": "503 Service Unavailable"}


# --- scoping --------------------------------------------------------------------


def test_no_spec_means_the_real_tool_runs(tool):
    """With nothing installed the wrapper calls straight through.

    Asserted against the bundled corpus rather than a stub, because "the wrapper is
    inert" is the property that makes it safe to wrap the deployed catalogue.
    """
    assert active_mocks() is None
    results = json.loads(call(tool, "funding"))["results"]
    assert results, "unmocked datasearch should hit the real corpus"
    assert "title" in results[0]


def test_an_unlisted_tool_is_not_mocked(tool):
    """A spec for another tool must not silently capture this one."""
    with using_mocks({"some_other_tool": "x"}):
        assert active_mocks() == {"some_other_tool": "x"}
        # datasearch is absent from the spec, so it runs for real.
        assert json.loads(call(tool, "funding"))["results"]


def test_mocks_do_not_leak_past_the_block(tool):
    with using_mocks({"datasearch": {"results": []}}):
        pass
    assert active_mocks() is None


def test_token_form_matches_the_context_manager(tool):
    token = install_mocks({"datasearch": "via token"})
    try:
        assert call(tool) == "via token"
    finally:
        restore_mocks(token)
    assert active_mocks() is None


def test_call_counts_reset_between_specs(tool):
    """Two examples in one experiment must not share a sequence position."""
    with using_mocks({"datasearch": [{"n": 1}, {"n": 2}]}):
        assert json.loads(call(tool))["n"] == 1
    with using_mocks({"datasearch": [{"n": 1}, {"n": 2}]}):
        assert json.loads(call(tool))["n"] == 1
