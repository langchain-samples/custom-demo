"""Contoso Fleet Ops: the MCP server a fleet-triage agent investigates through.

Stands in for the systems a Contoso Block Storage support engineer uses when an
anomaly comes in: the AIOps signal queue, Flex array records and telemetry,
controller log bundles, firmware source history, the engineering issue tracker,
and the handoff queue an investigation is filed to. All of it is read from the
seeded world in `fleet_mcp_server/fleet.py`, so every run sees the same fleet.

Stateless streamable HTTP, like the Meridian server next door. Run it with
`python -m fleet_mcp_server` (see `__main__.py`).

Log bundles are deliberately large (well over 100 KB for two hours). The agent's
harness offloads a result that size to its filesystem instead of reading it into
context, which is where the sandbox parses it with code.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from fleet_mcp_server import fleet as F

CACHE_TTL_SECONDS = int(os.getenv("FLEET_MCP_CACHE_TTL", "300"))

# Which engineering queue a filing lands in, by the component it blames.
_QUEUES = {
    "gc": "Block Storage - Space Management (GC)",
    "dedupe": "Block Storage - Data Reduction",
    "replication": "Block Storage - Replication",
    "cache": "Block Storage - Cache and NVRAM",
    "raid": "Block Storage - RAID and Media",
    "hardware": "Field Service - Proactive Parts",
    "nvme": "Block Storage - Host Connectivity",
    "iscsi": "Block Storage - Host Connectivity",
    "fc": "Block Storage - Host Connectivity",
    "mgmt": "Block Storage - Management Plane",
}

mcp = FastMCP(
    "Contoso Fleet Ops",
    instructions=(
        "Contoso Fleet Ops is the system of record for the installed base of Contoso block "
        "storage arrays: the AIOps anomaly queue, Flex array records and "
        "telemetry, controller log bundles, firmware source history, the engineering "
        "issue tracker, and the investigation handoff queue. Look every fact up here "
        "rather than assuming it.\n\n"
        "Timestamps are UTC ISO-8601. Telemetry is 1-minute samples; telemetry and log bundles are "
        "large, so parse them with code rather than reading them line by line. Log "
        "bundles cover at most 6 hours. Firmware versions are ordered "
        f"{' < '.join(F.FIRMWARE)}.\n\n"
        "`file_investigation` is the only tool that changes anything: it hands the "
        "finished investigation to an engineering queue (or closes it as not a defect). "
        "Call it once per signal, after the evidence is in."
    ),
    version="1.0.0",
    cache_ttl=CACHE_TTL_SECONDS,
    cache_scope="public",
)


def _unknown(kind: str, raw: str) -> dict[str, str]:
    """The error an agent sees for an id that does not exist."""
    return {"error": f"No {kind} {raw!r} in Contoso Fleet Ops."}


def _parse_time(raw: str) -> datetime | None:
    """Read an ISO-8601 timestamp, accepting a trailing Z."""
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


@mcp.tool(annotations={"readOnlyHint": True})
def list_open_signals(
    limit: Annotated[int, Field(ge=1, le=50, description="How many to return, newest first.")] = 10,
) -> dict[str, Any]:
    """List anomaly signals from the fleet detectors that nobody has investigated yet."""
    open_ = F.open_signals()
    return {
        "open": len(open_),
        "signals": [
            {
                k: asdict(s)[k]
                for k in ("signal_id", "serial", "detected_at", "detector", "metric", "confidence")
            }
            for s in open_[:limit]
        ],
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_signal(
    signal_id: Annotated[str, Field(description="An AIOps signal id, e.g. SIG-10737.")],
) -> dict[str, Any]:
    """Read one anomaly signal: which array, which metric, how far off baseline, and how confident."""
    signal = F.get_signal(signal_id)
    if signal is None:
        return _unknown("signal", signal_id)

    return asdict(signal)


@mcp.tool(annotations={"readOnlyHint": True})
def get_array(
    serial: Annotated[str, Field(description="Array serial number, e.g. CZ24W4FA6T.")],
) -> dict[str, Any]:
    """Look an array up in Flex: model, firmware and upgrade history, capacity, data services and workload."""
    a = F.get_array(serial)
    if a is None:
        return _unknown("array", serial)

    record = asdict(a)
    record.pop("degraded_bay")
    previous = (
        [{"version": a.previous_firmware, "until": a.firmware_upgraded_at}]
        if a.previous_firmware
        else []
    )
    record["firmware_history"] = [
        *previous,
        {"version": a.firmware, "installed_at": a.firmware_upgraded_at},
    ]
    return record


@mcp.tool(annotations={"readOnlyHint": True})
def get_telemetry(
    serial: Annotated[str, Field(description="Array serial number.")],
    end: Annotated[str, Field(description="End of the window, ISO-8601 UTC.")],
    hours: Annotated[int, Field(ge=1, le=72, description="Window length in hours.")] = 24,
    metrics: Annotated[
        list[str] | None,
        Field(description=f"Subset of: {', '.join(F.METRICS)}. Default: all."),
    ] = None,
) -> dict[str, Any]:
    """Fetch 1-minute performance telemetry for an array over a window.

    A full day is large. Save the result to a file and parse it with code rather
    than reading it into the conversation.
    """
    a = F.get_array(serial)
    if a is None:
        return _unknown("array", serial)

    at = _parse_time(end)
    if at is None:
        return {"error": f"end {end!r} is not an ISO-8601 timestamp."}

    chosen = metrics or list(F.METRICS)
    bad = [m for m in chosen if m not in F.METRICS]
    if bad:
        return {"error": f"Unknown metrics {bad}. Available: {list(F.METRICS)}."}

    return {"serial": a.serial, "interval": "1m", "points": F.telemetry(a, at, hours, chosen)}


@mcp.tool(annotations={"readOnlyHint": True})
def fetch_array_logs(
    serial: Annotated[str, Field(description="Array serial number.")],
    end: Annotated[str, Field(description="End of the window, ISO-8601 UTC.")],
    hours: Annotated[int, Field(ge=1, le=6, description="Window length in hours.")] = 2,
) -> str:
    """Pull the controller log bundle for an array: every subsystem, both controllers, one text file.

    Bundles are large. Save the result to a file and parse it with code (grep, awk,
    Python) rather than reading it into the conversation.
    """
    a = F.get_array(serial)
    if a is None:
        return _unknown("array", serial)["error"]

    at = _parse_time(end)
    if at is None:
        return f"end {end!r} is not an ISO-8601 timestamp."

    return F.logs(a, at, hours)


@mcp.tool(annotations={"readOnlyHint": True})
def list_firmware_changes(
    from_version: Annotated[str, Field(description="Release the array ran before.")],
    to_version: Annotated[str, Field(description="Release the array runs now.")],
) -> dict[str, Any]:
    """List the source commits that shipped between two firmware releases."""
    for v in (from_version, to_version):
        if v not in F.FIRMWARE:
            return {"error": f"Unknown firmware {v!r}. Releases: {list(F.FIRMWARE)}."}

    if F.FIRMWARE.index(from_version) >= F.FIRMWARE.index(to_version):
        return {"error": "from_version must be older than to_version."}

    return {
        "from": from_version,
        "to": to_version,
        "commits": [
            {
                "sha": c.sha,
                "version": c.version,
                "component": c.component,
                "title": c.title,
                "author": c.author,
            }
            for c in F.changes_between(from_version, to_version)
        ],
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_commit_diff(
    sha: Annotated[str, Field(description="Commit sha or unique prefix.")],
) -> dict[str, Any]:
    """Read the diff for one firmware commit."""
    commit = F.get_commit(sha)
    if commit is None:
        return _unknown("commit", sha)

    return asdict(commit)


@mcp.tool(annotations={"readOnlyHint": True})
def query_fleet(
    firmware: Annotated[str | None, Field(description="Exact firmware release.")] = None,
    workload: Annotated[str | None, Field(description="Workload profile, e.g. SQL OLTP.")] = None,
    min_capacity_pct: Annotated[
        float | None, Field(description="Minimum capacity used, percent.")
    ] = None,
    dedupe_enabled: Annotated[bool | None, Field(description="Dedupe on or off.")] = None,
    min_replicated_volumes: Annotated[
        int | None, Field(description="Minimum replicated volumes.")
    ] = None,
) -> dict[str, Any]:
    """Count arrays in a fleet cohort and how many of them raised latency signals in the last 7 days.

    Compare two cohorts that differ in one attribute (say, firmware, or capacity
    above and below a threshold) to test whether that attribute explains a signal.
    """
    return F.query_fleet(
        firmware, workload, min_capacity_pct, dedupe_enabled, min_replicated_volumes
    )


@mcp.tool(annotations={"readOnlyHint": True})
def search_issues(
    query: Annotated[str, Field(description="Symptoms, component or versions in plain words.")],
    component: Annotated[str | None, Field(description="Restrict to one component.")] = None,
) -> dict[str, Any]:
    """Search the engineering issue tracker for known issues matching a symptom, to tag duplicates."""
    return {"results": [asdict(i) for i in F.search_issues(query, component)]}


@mcp.tool(annotations={"readOnlyHint": True})
def get_issue(
    key: Annotated[str, Field(description="Issue key, e.g. STOR-48213.")],
) -> dict[str, Any]:
    """Read one issue from the tracker."""
    issue = F.get_issue(key)
    if issue is None:
        return _unknown("issue", key)

    return asdict(issue)


@mcp.tool
def file_investigation(
    signal_id: Annotated[str, Field(description="The signal this investigation answers.")],
    verdict: Annotated[
        Literal["new_defect", "duplicate_of_known_issue", "hardware_fault", "not_a_defect"],
        Field(description="What the signal turned out to be."),
    ],
    recommendation: Annotated[
        F.Recommendation,
        Field(description="The single next action you recommend."),
    ],
    component: Annotated[
        str,
        Field(description=f"Component at fault: one of {sorted(_QUEUES)}, or 'none'."),
    ],
    root_cause: Annotated[
        str, Field(description="One or two sentences: the mechanism, not the symptom.")
    ],
    confidence: Annotated[Literal["low", "medium", "high"], Field(description="How sure you are.")],
    handoff_markdown: Annotated[
        str, Field(description="The full engineer-ready handoff package, in Markdown.")
    ],
    suspected_commit: Annotated[
        str | None, Field(description="Firmware commit sha, if a code change is implicated.")
    ] = None,
    duplicate_of: Annotated[
        str | None, Field(description="Existing issue key this duplicates, if any.")
    ] = None,
    artifacts: Annotated[
        list[str] | None, Field(description="Paths of the evidence files the handoff cites.")
    ] = None,
) -> dict[str, Any]:
    """Hand a finished investigation to engineering, or close it as not a defect. Call once per signal."""
    signal = F.get_signal(signal_id)
    if signal is None:
        return _unknown("signal", signal_id)

    # One filing per signal: a second call (say, after a rubric pass sends the agent back)
    # would put the same investigation in an engineering queue twice.
    filed = next((f for f in F.filings() if f.get("signal_id") == signal.signal_id), None)
    if filed:
        return {
            "error": f"{signal.signal_id} is already filed as {filed['investigation_id']}. "
            "Do not file it again; update the handoff file instead.",
        }

    if suspected_commit and F.get_commit(suspected_commit) is None:
        return _unknown("commit", suspected_commit)

    if duplicate_of and F.get_issue(duplicate_of) is None:
        return _unknown("issue", duplicate_of)

    component = component.strip().lower()
    if component != "none" and component not in _QUEUES:
        return {
            "error": f"Unknown component {component!r}. Use one of {sorted(_QUEUES)} or 'none'."
        }

    commit = F.get_commit(suspected_commit) if suspected_commit else None
    queue = (
        "Closed - detector feedback logged"
        if recommendation == "dismiss"
        else _QUEUES.get(component, "Triage")
    )
    record = F.record_filing(
        {
            "signal_id": signal.signal_id,
            "serial": signal.serial,
            "verdict": verdict,
            "recommendation": recommendation,
            "component": component,
            "root_cause": root_cause,
            "confidence": confidence,
            "suspected_commit": commit.sha if commit else None,
            "duplicate_of": duplicate_of.strip().upper() if duplicate_of else None,
            "artifacts": artifacts or [],
            "handoff_markdown": handoff_markdown,
            "routed_to": queue,
        }
    )
    return {
        "investigation_id": record["investigation_id"],
        "routed_to": queue,
        "sla": "next business day" if recommendation != "dismiss" else None,
        "follow_up_by": (
            (signal.at() + timedelta(days=1)).isoformat() if recommendation != "dismiss" else None
        ),
    }
