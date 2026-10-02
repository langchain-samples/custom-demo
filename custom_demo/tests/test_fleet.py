"""Contoso Fleet Ops: the seeded fleet and the MCP server the triage demo investigates through.

The fleet is the demo's answer key, so these pin what a presenter relies on: every
planted story has arrays and signals behind it, a signal's figures reproduce from the
array's own telemetry, the evidence each story needs is actually in its logs, and the
ground truth never reaches the agent.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta

import pytest
from fastmcp import Client

from fleet_mcp_server import fleet as F
from fleet_mcp_server.server import mcp


@pytest.fixture(autouse=True)
def _filings(tmp_path, monkeypatch):
    """Point the filings log at a temp file so tests never touch demo state."""
    monkeypatch.setattr(F, "FILINGS_PATH", tmp_path / "filings.jsonl")


def _first(cause: str) -> F.Signal:
    """The newest signal planted with `cause`."""
    return next(s for s in F.signals() if (t := F.ground_truth(s.signal_id)) and t.cause == cause)


def test_every_planted_story_has_signals():
    causes = {t.cause for s in F.signals() if (t := F.ground_truth(s.signal_id))}
    assert causes == {F.GC, F.DEDUPE, F.SNAPSYNC, F.SSD, F.BENIGN}


def test_signal_figures_reproduce_from_telemetry():
    signal = F.signals()[0]
    a = F.get_array(signal.serial)
    assert a is not None
    points = [p[signal.metric] for p in F.telemetry(a, signal.at(), 24, [signal.metric])]
    assert signal.observed in points
    assert signal.observed >= F.DETECTOR_RATIO * signal.baseline


def test_five_minute_series_is_a_subset_of_the_one_minute_series():
    a = F.arrays()[0]
    end = F.signals()[0].at()
    fine = {p["ts"]: p for p in F.telemetry(a, end, 2, ["write_latency_ms"])}
    coarse = F.telemetry(a, end, 2, ["write_latency_ms"], step_minutes=5)
    # The 5-minute series floors its end, so its first sample can precede the 1-minute
    # window; every sample the two share must match.
    shared = [p for p in coarse if p["ts"] in fine]
    assert len(shared) >= len(coarse) - 1
    assert all(fine[p["ts"]] == p for p in shared)


@pytest.mark.parametrize(
    ("cause", "evidence"),
    [
        (F.GC, r"fg_yield=skipped"),
        (F.DEDUPE, r"fpidx\[\d+\]: WARN  miss_ratio"),
        (F.SSD, r"hw\[\d+\]: WARN  drive bay="),
    ],
)
def test_the_logs_carry_each_storys_evidence(cause, evidence):
    signal = _first(cause)
    a = F.get_array(signal.serial)
    assert a is not None
    bundle = F.logs(a, signal.at() + timedelta(minutes=30), 2)
    assert re.search(evidence, bundle)


def test_implicated_commits_are_in_the_upgrade_range():
    for cause, previous, current in (
        (F.GC, "6.1.1.200", "6.1.2.100"),
        (F.SNAPSYNC, "6.1.1.100", "6.1.1.200"),
    ):
        truth = F.ground_truth(_first(cause).signal_id)
        assert truth is not None
        assert truth.commit in {c.sha for c in F.changes_between(previous, current)}


def test_no_tool_exposes_ground_truth_or_the_bad_drive():
    signal = _first(F.SSD)

    async def run() -> list[str]:
        async with Client(mcp) as client:
            outputs = []
            for name, args in (
                ("get_signal", {"signal_id": signal.signal_id}),
                ("get_array", {"serial": signal.serial}),
                ("list_open_signals", {"limit": 50}),
            ):
                result = await client.call_tool(name, args)
                outputs.append(json.dumps(result.structured_content))

            return outputs

    for text in asyncio.run(run()):
        assert "degraded_bay" not in text
        assert F.SSD not in text
        assert "recommendation" not in text


def test_filing_closes_the_signal_once_and_rejects_unknown_references():
    signal = F.signals()[0]
    dismiss = {
        "signal_id": signal.signal_id,
        "verdict": "not_a_defect",
        "recommendation": "dismiss",
        "component": "none",
        "root_cause": "backup job",
        "confidence": "high",
        "handoff_markdown": "# dismissed",
    }

    async def run() -> tuple[dict, dict, dict, int]:
        async with Client(mcp) as client:
            bad = await client.call_tool(
                "file_investigation",
                {
                    "signal_id": signal.signal_id,
                    "verdict": "duplicate_of_known_issue",
                    "recommendation": "escalate_engineering",
                    "component": "gc",
                    "root_cause": "x",
                    "confidence": "low",
                    "handoff_markdown": "# x",
                    "duplicate_of": "STOR-00000",
                },
            )
            good = await client.call_tool("file_investigation", dismiss)
            again = await client.call_tool("file_investigation", dismiss)
            still_open = await client.call_tool("list_open_signals", {"limit": 50})
            return (
                bad.structured_content,
                good.structured_content,
                again.structured_content,
                still_open.structured_content["open"],
            )

    bad, good, again, still_open = asyncio.run(run())
    assert "error" in bad
    assert good["investigation_id"].startswith("INV-")
    assert good["investigation_id"] in again["error"]
    assert still_open == len(F.signals()) - 1
    assert F.filings()[-1]["signal_id"] == signal.signal_id
