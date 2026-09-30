"""The seeded HPE block-storage fleet behind the triage demo's MCP server.

Everything here is generated deterministically from `HPE_FLEET_SEED`, so every
process (the MCP server, the fleet driver, a test) sees the same arrays, signals,
logs and firmware history. The world has four planted stories, each tied to a real
line of code in a firmware release, plus benign noise the agent should dismiss:

  * `gc_reclaim_budget` - commit a41f9c2 in 6.1.2.100 lets garbage collection run
    at full budget without yielding to foreground writes once free segments drop
    under 12%. Write latency spikes, but only on write-heavy arrays (SQL OLTP, VDI)
    that are over 80% full. An open issue, STOR-48213, describes the symptom with no
    root cause yet.
  * `fp_index_cache` - commit 7c03e18 in 6.1.2.100 shrinks the dedupe fingerprint
    index cache to make room for a new metadata reservation. Read latency climbs on
    arrays with dedupe on and a high dedupe ratio. No issue exists: this is new.
  * `snapsync_top_of_hour` - commit e92b5d0 in 6.1.1.200 aligns replication
    schedules to the top of the hour, so arrays with many replicated volumes freeze
    dozens of snapshots at once. Known as STOR-47102 and fixed in 6.1.2.100.
  * `degraded_ssd` - a handful of arrays have one S6X drive wearing out and throwing
    media errors. Not a firmware problem at all: it matches the open drive-batch issue
    STOR-46233, and the fix is a drive swap.
  * `workload_change` - a backup job, a VM migration or a batch load explains the
    anomaly. Nothing is wrong; the right call is to dismiss.

The tools only ever expose what an engineer could observe. `ground_truth` is for
the driver's scoring and is never served over MCP.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal

FLEET_SEED = int(os.getenv("HPE_FLEET_SEED", "5150"))
FLEET_SIZE = 200
SIGNAL_COUNT = 400

# How far above its 24h median a 5-minute sample has to be before a detector fires.
DETECTOR_RATIO = 1.5

# Where `file_investigation` records its handoffs. Outside the repo on purpose: the
# filings are demo state that the driver reads back to score runs, not source.
FILINGS_PATH = Path(
    os.getenv("HPE_FILINGS_PATH", str(Path.home() / ".cache" / "hpe-fleet" / "filings.jsonl"))
)

FIRMWARE = ("6.1.1.100", "6.1.1.200", "6.1.2.100", "6.1.2.200")

GC = "gc_reclaim_budget"
DEDUPE = "fp_index_cache"
SNAPSYNC = "snapsync_top_of_hour"
SSD = "degraded_ssd"
BENIGN = "workload_change"

Recommendation = Literal[
    "escalate_engineering", "upgrade_firmware", "apply_workaround", "replace_hardware", "dismiss"
]

_WRITE_HEAVY = ("SQL OLTP", "VDI")
_GC_FIRMWARE = ("6.1.2.100", "6.1.2.200")


def _anchor() -> datetime:
    """The fleet's "now": midnight UTC today, so the data looks current but is stable all day."""
    fixed = os.getenv("HPE_FLEET_ANCHOR")
    if fixed:
        return datetime.fromisoformat(fixed).astimezone(UTC)

    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _rng(*parts: object) -> random.Random:
    """A Random seeded from the fleet seed plus `parts`, stable across processes."""
    key = ":".join(str(p) for p in (FLEET_SEED, *parts))
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


def _iso(ts: datetime) -> str:
    """Render a timestamp the way the array logs do."""
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


# --------------------------------------------------------------------------- #
# Arrays                                                                        #
# --------------------------------------------------------------------------- #

_MODELS = (
    ("HPE Alletra 6030", 2, 24),
    ("HPE Alletra 6050", 2, 24),
    ("HPE Alletra 6070", 2, 48),
    ("HPE Alletra 6090", 2, 48),
    ("HPE Alletra Storage MP B10000", 4, 48),
)

_CUSTOMERS = (
    ("Northwind Health", "Omaha, NE"),
    ("Contoso Financial", "Charlotte, NC"),
    ("Fabrikam Manufacturing", "Dayton, OH"),
    ("Tailspin Airlines", "Dallas, TX"),
    ("Wide World Importers", "Seattle, WA"),
    ("Adventure Works Retail", "Denver, CO"),
    ("Litware Insurance", "Hartford, CT"),
    ("Proseware Labs", "Raleigh, NC"),
    ("Woodgrove Bank", "Toronto, ON"),
    ("Fourth Coffee", "Portland, OR"),
    ("Alpine Ski House", "Salt Lake City, UT"),
    ("Blue Yonder Freight", "Memphis, TN"),
    ("Coho Winery", "Napa, CA"),
    ("Datum Energy", "Houston, TX"),
    ("Graphic Design Institute", "Providence, RI"),
    ("Humongous Insurance", "Des Moines, IA"),
    ("Lamna Healthcare", "Minneapolis, MN"),
    ("Margie's Travel", "Orlando, FL"),
    ("Munson's Pickles", "Milwaukee, WI"),
    ("Relecloud Media", "Atlanta, GA"),
    ("Southridge Video", "Burbank, CA"),
    ("Trey Research", "Boston, MA"),
    ("VanArsdel Foods", "Chicago, IL"),
    ("Wingtip Toys", "Phoenix, AZ"),
    ("Bellows College", "Madison, WI"),
    ("First Up Consultants", "London, UK"),
    ("Nod Publishers", "Frankfurt, DE"),
    ("School of Fine Art", "Paris, FR"),
)

_WORKLOADS = (
    ("SQL OLTP", 0.22),
    ("VDI", 0.16),
    ("VMware mixed", 0.24),
    ("Oracle DW", 0.10),
    ("Backup target", 0.09),
    ("File services", 0.10),
    ("Analytics", 0.09),
)


@dataclass(frozen=True)
class Array:
    """One array at a customer site, as GreenLake knows it."""

    serial: str
    model: str
    controllers: int
    drive_count: int
    customer: str
    site: str
    workload: str
    firmware: str
    previous_firmware: str | None
    firmware_upgraded_at: str
    capacity_tb: float
    capacity_used_pct: float
    dedupe_enabled: bool
    dedupe_ratio: float
    volumes: int
    replicated_volumes: int
    degraded_bay: int | None

    def upgraded_at(self) -> datetime:
        """When the running firmware was installed."""
        return datetime.fromisoformat(self.firmware_upgraded_at)


def _serial(rng: random.Random, taken: set[str]) -> str:
    """An HPE-style array serial, unique within the fleet."""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    while True:
        serial = (
            f"CZ{rng.randint(21, 26)}{rng.choice(letters)}{rng.randint(0, 9)}"
            f"{rng.choice(letters)}{rng.choice(letters)}{rng.randint(0, 9)}{rng.choice(letters)}"
        )
        if serial not in taken:
            taken.add(serial)
            return serial


def _previous(firmware: str) -> str | None:
    """The release an array most plausibly upgraded from."""
    index = FIRMWARE.index(firmware)
    return FIRMWARE[index - 1] if index else None


@cache
def arrays() -> tuple[Array, ...]:
    """The whole fleet, in serial order."""
    rng = _rng("arrays")
    anchor = _anchor()
    taken: set[str] = set()
    fleet: list[Array] = []
    for _ in range(FLEET_SIZE):
        model, controllers, drives = rng.choice(_MODELS)
        customer, site = rng.choice(_CUSTOMERS)
        workload = rng.choices([w for w, _ in _WORKLOADS], [p for _, p in _WORKLOADS])[0]
        firmware = rng.choices(FIRMWARE, [0.18, 0.30, 0.37, 0.15])[0]
        recent = firmware in _GC_FIRMWARE
        upgraded_days = rng.randint(6, 58) if recent else rng.randint(70, 320)
        dedupe = rng.random() < {"VDI": 0.9, "VMware mixed": 0.6}.get(workload, 0.25)
        ratio = rng.uniform(4.2, 7.6) if workload == "VDI" else rng.uniform(1.3, 3.6)
        heavy_replication = rng.random() < 0.3
        volumes = rng.randint(40, 260)
        fleet.append(
            Array(
                serial=_serial(rng, taken),
                model=model,
                controllers=controllers,
                drive_count=drives,
                customer=customer,
                site=site,
                workload=workload,
                firmware=firmware,
                previous_firmware=_previous(firmware),
                firmware_upgraded_at=(
                    anchor - timedelta(days=upgraded_days, hours=rng.randint(1, 20))
                ).isoformat(),
                capacity_tb=float(rng.choice((92, 184, 368, 736, 1472))),
                capacity_used_pct=round(rng.uniform(44, 94), 1),
                dedupe_enabled=dedupe,
                dedupe_ratio=round(ratio if dedupe else 1.0, 2),
                volumes=volumes,
                replicated_volumes=min(
                    volumes, rng.randint(48, 120) if heavy_replication else rng.randint(0, 36)
                ),
                degraded_bay=None,
            )
        )

    # A drive going bad is independent of firmware, so pick its victims from the whole
    # fleet after everything else is settled.
    for index in rng.sample(range(len(fleet)), 5):
        a = fleet[index]
        fleet[index] = replace(a, degraded_bay=rng.randint(1, a.drive_count))

    return tuple(sorted(fleet, key=lambda a: a.serial))


@cache
def _by_serial() -> dict[str, Array]:
    """Serial to array."""
    return {a.serial: a for a in arrays()}


def get_array(serial: str) -> Array | None:
    """Look an array up by serial, case-insensitively."""
    return _by_serial().get(serial.strip().upper())


def conditions(a: Array) -> set[str]:
    """Which planted defects this array actually has (never served over MCP)."""
    found: set[str] = set()
    if a.firmware in _GC_FIRMWARE and a.workload in _WRITE_HEAVY and a.capacity_used_pct >= 80:
        found.add(GC)

    if a.firmware in _GC_FIRMWARE and a.dedupe_enabled and a.dedupe_ratio >= 4.0:
        found.add(DEDUPE)

    if a.firmware == "6.1.1.200" and a.replicated_volumes >= 48:
        found.add(SNAPSYNC)

    if a.degraded_bay is not None:
        found.add(SSD)

    return found


# --------------------------------------------------------------------------- #
# Signals: what the existing AIOps detectors raise                             #
# --------------------------------------------------------------------------- #

_CAUSE_WEIGHTS = ((GC, 0.22), (DEDUPE, 0.14), (SNAPSYNC, 0.15), (SSD, 0.06), (BENIGN, 0.43))

_BENIGN_KINDS = ("backup_job", "vm_migration", "batch_load")


@dataclass(frozen=True)
class Signal:
    """One low-confidence anomaly from the fleet detectors."""

    signal_id: str
    serial: str
    detected_at: str
    detector: str
    metric: str
    baseline: float
    observed: float
    confidence: float
    duration_minutes: int
    summary: str

    def at(self) -> datetime:
        """When the detector fired."""
        return datetime.fromisoformat(self.detected_at)


@dataclass(frozen=True)
class _Planted:
    """The hidden story behind one signal."""

    cause: str
    benign_kind: str | None


def _signal_for(i: int, cause: str, a: Array, rng: random.Random, anchor: datetime) -> Signal:
    """Render the detector's view of one planted story on one array."""
    minutes_ago = rng.randint(15, 7 * 24 * 60)
    at = anchor - timedelta(minutes=minutes_ago)
    if cause == SNAPSYNC:
        at = at.replace(minute=rng.randint(0, 6))
    elif cause in (GC, DEDUPE):
        # Both bite hardest at the afternoon peak (see `_business_load`), which is when a
        # detector watching them fires.
        at = at.replace(hour=rng.randint(15, 19))

    if a.firmware in _GC_FIRMWARE and at < a.upgraded_at():
        at = a.upgraded_at() + timedelta(hours=rng.randint(12, 60))

    metric = {
        GC: "write_latency_ms",
        DEDUPE: "read_latency_ms",
        SNAPSYNC: "write_latency_ms",
        SSD: "read_latency_ms",
    }.get(cause) or rng.choice(("read_latency_ms", "write_latency_ms"))
    detector = {
        GC: "latency_anomaly",
        DEDUPE: "latency_anomaly",
        SNAPSYNC: "io_pattern_drift",
        SSD: "latency_anomaly",
    }.get(cause) or rng.choice(("latency_anomaly", "io_pattern_drift", "workload_correlation"))
    correlation = rng.choice(
        (
            "workload correlation inconclusive",
            "no matching known-issue signature",
            "resource contention score moderate",
            "below paging threshold",
        )
    )
    # Baseline, observed and the summary are measured off the array's own telemetry in
    # `signals()`, so the detector never reports a number the agent cannot reproduce.
    return Signal(
        signal_id=f"SIG-{10412 + i}",
        serial=a.serial,
        detected_at=at.isoformat(),
        detector=detector,
        metric=metric,
        baseline=0.0,
        observed=0.0,
        confidence=round(rng.uniform(0.18, 0.58), 2),
        duration_minutes=rng.choice((15, 20, 35, 45, 70, 95)),
        summary=correlation,
    )


@cache
def _signals_and_truth() -> tuple[tuple[Signal, ...], dict[str, _Planted]]:
    """Every signal (newest first) and the story planted behind each."""
    rng = _rng("signals")
    anchor = _anchor()
    fleet = arrays()
    pools = {cause: [a for a in fleet if cause in conditions(a)] for cause, _ in _CAUSE_WEIGHTS}
    pools[BENIGN] = [a for a in fleet if not conditions(a)]
    signals: list[Signal] = []
    planted: dict[str, _Planted] = {}
    for i in range(SIGNAL_COUNT):
        cause = rng.choices([c for c, _ in _CAUSE_WEIGHTS], [w for _, w in _CAUSE_WEIGHTS])[0]
        if not pools[cause]:
            cause = BENIGN

        signal = _signal_for(i, cause, rng.choice(pools[cause]), rng, anchor)
        signals.append(signal)
        planted[signal.signal_id] = _Planted(
            cause=cause, benign_kind=rng.choice(_BENIGN_KINDS) if cause == BENIGN else None
        )

    signals.sort(key=lambda s: s.detected_at, reverse=True)
    return tuple(signals), planted


@cache
def signals() -> tuple[Signal, ...]:
    """Every signal the detectors raised in the last seven days, newest first."""
    measured = (_measured(s) for s in _signals_and_truth()[0])
    # A detector only fires on a real excursion; drop draws that landed in a quiet spell.
    return tuple(s for s in measured if s.observed >= DETECTOR_RATIO * s.baseline)


def _measured(raw: Signal) -> Signal:
    """Fill a signal's baseline, peak and summary from the array's telemetry."""
    a = _by_serial()[raw.serial]
    points = [p[raw.metric] for p in telemetry(a, raw.at(), 24, [raw.metric], step_minutes=5)]
    window = max(3, raw.duration_minutes // 5)
    before = sorted(points[:-window])
    baseline = before[len(before) // 2]
    observed = max(points[-window:])
    summary = (
        f"5-min {raw.metric} peaked at {observed} ms against a 24h median of {baseline} ms "
        f"over {raw.duration_minutes} min on {a.model} ({a.workload}); {raw.summary}."
    )
    return replace(raw, baseline=baseline, observed=observed, summary=summary)


def get_signal(signal_id: str) -> Signal | None:
    """Look a signal up by id, case-insensitively."""
    wanted = signal_id.strip().upper()
    return next((s for s in signals() if s.signal_id == wanted), None)


def _benign_events(serial: str) -> list[tuple[datetime, datetime, str]]:
    """Workload changes on this array: (start, end, kind), one per benign signal."""
    raw, planted = _signals_and_truth()
    events = []
    for s in raw:
        story = planted[s.signal_id]
        if s.serial == serial and story.benign_kind:
            start = s.at() - timedelta(minutes=18)
            events.append(
                (start, start + timedelta(minutes=s.duration_minutes + 40), story.benign_kind)
            )

    return events


# --------------------------------------------------------------------------- #
# Ground truth: for scoring only                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Truth:
    """The right answer for one signal: what a correct investigation files."""

    cause: str
    component: str
    commit: str | None
    duplicate_of: str | None
    recommendation: Recommendation


_TRUTH = {
    GC: Truth(GC, "gc", "a41f9c2", "STOR-48213", "escalate_engineering"),
    DEDUPE: Truth(DEDUPE, "dedupe", "7c03e18", None, "escalate_engineering"),
    SNAPSYNC: Truth(SNAPSYNC, "replication", "e92b5d0", "STOR-47102", "upgrade_firmware"),
    SSD: Truth(SSD, "hardware", None, "STOR-46233", "replace_hardware"),
    BENIGN: Truth(BENIGN, "none", None, None, "dismiss"),
}


_BENIGN_DUPLICATES = {"backup_job": "STOR-47655", "vm_migration": "STOR-48301"}


def ground_truth(signal_id: str) -> Truth | None:
    """The planted answer for a signal. Never expose this through the MCP server."""
    story = _signals_and_truth()[1].get(signal_id.strip().upper())
    if story is None:
        return None

    truth = _TRUTH[story.cause]
    # Backups and migrations each have a closed "not a defect" issue, and tagging it is the
    # right call; a batch load has none.
    known = _BENIGN_DUPLICATES.get(story.benign_kind or "")
    return replace(truth, duplicate_of=known) if known else truth


# --------------------------------------------------------------------------- #
# Telemetry                                                                     #
# --------------------------------------------------------------------------- #

METRICS = (
    "read_latency_ms",
    "write_latency_ms",
    "read_iops",
    "write_iops",
    "read_mbps",
    "write_mbps",
    "cache_hit_pct",
    "cpu_pct",
)

_IOPS_BASE = {
    "SQL OLTP": (38000, 26000),
    "VDI": (30000, 21000),
    "VMware mixed": (24000, 14000),
    "Oracle DW": (16000, 6000),
    "Backup target": (4000, 9000),
    "File services": (9000, 5000),
    "Analytics": (21000, 4000),
}


def _business_load(ts: datetime) -> float:
    """0..1 diurnal load, peaking mid-afternoon UTC (US business hours)."""
    hour = ts.hour + ts.minute / 60
    return max(0.15, 1 - abs(hour - 17.5) / 9) if ts.weekday() < 5 else 0.3


def _point(a: Array, ts: datetime, events: list[tuple[datetime, datetime, str]]) -> dict[str, Any]:
    """One telemetry sample, with every planted condition applied."""
    rng = _rng("telemetry", a.serial, ts.isoformat())
    load = _business_load(ts)
    r_iops, w_iops = _IOPS_BASE[a.workload]
    read_iops = r_iops * (0.35 + 0.65 * load) * rng.uniform(0.92, 1.08)
    write_iops = w_iops * (0.35 + 0.65 * load) * rng.uniform(0.92, 1.08)
    read_lat = rng.uniform(0.55, 0.85) * (1 + 0.25 * load)
    write_lat = rng.uniform(0.35, 0.6) * (1 + 0.25 * load)
    cache_hit = rng.uniform(89, 95)
    cpu = 22 + 38 * load + rng.uniform(-4, 4)
    read_mbps = read_iops * 16 / 1024
    write_mbps = write_iops * 16 / 1024
    found = conditions(a)
    after_upgrade = ts >= a.upgraded_at()
    if GC in found and after_upgrade and load > 0.55:
        write_lat *= 1 + 5.5 * (load - 0.5) * rng.uniform(0.8, 1.3)
        cpu += 14 * load

    if DEDUPE in found and after_upgrade:
        cache_hit -= 22 * (0.4 + 0.6 * load)
        read_lat *= 1 + 2.4 * load * rng.uniform(0.85, 1.2)

    if SNAPSYNC in found and ts.minute < 10:
        write_lat *= rng.uniform(3.2, 5.0)
        write_iops *= 0.72
        cpu += 18

    if SSD in found:
        read_lat *= rng.uniform(1.3, 1.6) if rng.random() > 0.15 else rng.uniform(3.8, 6.5)

    for start, end, kind in events:
        if start <= ts < end:
            if kind == "backup_job":
                read_mbps *= 4.2
                read_lat *= 1.9
            elif kind == "vm_migration":
                write_iops *= 2.3
                write_mbps *= 3.1
                write_lat *= 2.1
            else:
                write_mbps *= 3.6
                read_iops *= 1.7
                read_lat *= 1.6
                write_lat *= 1.8

            cpu += 16

    return {
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "read_latency_ms": round(read_lat, 2),
        "write_latency_ms": round(write_lat, 2),
        "read_iops": int(read_iops),
        "write_iops": int(write_iops),
        "read_mbps": round(read_mbps, 1),
        "write_mbps": round(write_mbps, 1),
        "cache_hit_pct": round(max(40.0, cache_hit), 1),
        "cpu_pct": round(min(99.0, cpu), 1),
    }


def telemetry(
    a: Array, end: datetime, hours: int, metrics: list[str], step_minutes: int = 1
) -> list[dict[str, Any]]:
    """Samples every `step_minutes` for `hours` ending at `end`, restricted to `metrics`.

    Each sample is seeded by its own timestamp, so a 5-minute series is exactly every
    fifth point of the 1-minute one: the detector's figures reproduce from either.
    """
    events = _benign_events(a.serial)
    end = end.replace(minute=end.minute - end.minute % step_minutes, second=0, microsecond=0)
    points = []
    for step in range(hours * 60 // step_minutes, -1, -1):
        full = _point(a, end - timedelta(minutes=step_minutes * step), events)
        points.append({"ts": full["ts"], **{m: full[m] for m in metrics}})

    return points


# --------------------------------------------------------------------------- #
# Logs                                                                          #
# --------------------------------------------------------------------------- #

_HOSTS = ("esx-r12-04", "esx-r12-07", "sqlprd-02", "sqlprd-05", "vdi-pool-a", "orcl-dw-01")


def _noise(rng: random.Random, a: Array, ctrl: str) -> str:
    """One routine log line from a subsystem that has nothing to do with the incident."""
    vol = f"vol-{rng.randint(1, a.volumes):04d}"
    choices = (
        lambda: (
            f"iscsi[1187]: INFO  session refresh initiator=iqn.1998-01.com.vmware:{rng.choice(_HOSTS)} tsih={rng.randint(1, 900)}"
        ),
        lambda: (
            f"fc[1203]: INFO  port {ctrl.lower()}{rng.randint(1, 4)} link up 32G fabric=B flogi ok"
        ),
        lambda: (
            f"mgmt[902]: INFO  api GET /v1/volumes/{vol} user=greenlake-collector status=200 ms={rng.randint(3, 40)}"
        ),
        lambda: (
            f"cache[1450]: INFO  nvram flush seq={rng.randint(10**6, 10**7)} dirty_pct={rng.uniform(4, 22):.1f}"
        ),
        lambda: (
            f"raid[1322]: INFO  scrub pass progress={rng.uniform(0, 100):.1f}% group=rg{rng.randint(0, 3)}"
        ),
        lambda: (
            f"gc[2211]: INFO  reclaim cycle seg_free_ratio={rng.uniform(0.16, 0.34):.3f} budget_mbps={rng.randint(180, 520)} fg_wq_depth={rng.randint(2, 24)} fg_yield=applied"
        ),
        lambda: (
            f"fpidx[2290]: INFO  stats lookups/s={rng.randint(20000, 90000)} miss_ratio={rng.uniform(0.02, 0.07):.3f} ddr={a.dedupe_ratio}"
        ),
        lambda: (
            f"snapsync[2402]: INFO  replicate ok vol={vol} partner=dr-{a.serial[-4:].lower()} delta_mb={rng.randint(20, 900)}"
        ),
        lambda: f"ntp[610]: INFO  offset={rng.uniform(-0.8, 0.8):.3f}ms stratum=2",
        lambda: (
            f"hw[733]: INFO  env psu{rng.randint(1, 2)} ok temp_c={rng.randint(24, 33)} fan_rpm={rng.randint(5200, 6900)}"
        ),
        lambda: (
            f"trace[3001]: DEBUG io_path vol={vol} q_us={rng.randint(20, 140)} media_us={rng.randint(80, 260)} gc_wait_us={rng.randint(0, 30)} dedupe_us={rng.randint(5, 60)}"
        ),
    )
    return rng.choice(choices)()


def _evidence(rng: random.Random, a: Array, ts: datetime, ctrl: str, events: list) -> list[str]:
    """Lines a planted condition writes at `ts`, if it is active then."""
    found = conditions(a)
    load = _business_load(ts)
    after_upgrade = ts >= a.upgraded_at()
    vol = f"vol-{rng.randint(1, a.volumes):04d}"
    lines: list[str] = []
    if GC in found and after_upgrade and load > 0.55 and rng.random() < 0.18:
        lines.append(
            f"gc[2211]: INFO  reclaim cycle seg_free_ratio={rng.uniform(0.085, 0.118):.3f} "
            f"budget_mbps=1600 (GC_BUDGET_MAX) fg_wq_depth={rng.randint(140, 260)} fg_yield=skipped"
        )
        if rng.random() < 0.5:
            lines.append(
                f"trace[3001]: DEBUG io_path vol={vol} q_us={rng.randint(900, 4000)} "
                f"media_us={rng.randint(90, 240)} gc_wait_us={rng.randint(6000, 19000)} dedupe_us={rng.randint(5, 60)}"
            )

        if rng.random() < 0.3:
            lines.append(
                f"wlat[3120]: WARN  p99 write ack {rng.uniform(6, 21):.1f}ms exceeds SLO 5.0ms vol={vol}"
            )

    if DEDUPE in found and after_upgrade and rng.random() < 0.12:
        lines.append(
            f"fpidx[2290]: WARN  miss_ratio={rng.uniform(0.28, 0.46):.3f} target<0.100 "
            f"evictions/s={rng.randint(3000, 9000)} cache_budget=11%dram ddr={a.dedupe_ratio}"
        )
        if rng.random() < 0.4:
            lines.append(
                f"trace[3001]: DEBUG io_path vol={vol} q_us={rng.randint(40, 200)} "
                f"media_us={rng.randint(900, 3200)} gc_wait_us={rng.randint(0, 30)} dedupe_us={rng.randint(2400, 7800)}"
            )

    if SNAPSYNC in found and ts.minute < 3 and rng.random() < 0.35:
        n = a.replicated_volumes
        lines.append(
            f"snapsync[2402]: INFO  schedule batch start schedules={n} aligned={ts:%H}:00 coalesced=true"
        )
        lines.append(
            f"snapsync[2402]: WARN  {n} concurrent snapshot freezes, write ack stalled {rng.randint(28, 70)}ms"
        )

    if SSD in found and rng.random() < 0.05:
        bay = a.degraded_bay
        lines.append(
            f"hw[733]: WARN  drive bay={bay} sn=S6X{a.serial[-5:]}Q media_err corrected={rng.randint(40, 400)} "
            f"reallocated={rng.randint(12, 90)} smart_wear=91%"
        )
        if rng.random() < 0.5:
            lines.append(
                f"raid[1322]: WARN  slow member bay={bay} service_time_p99={rng.randint(22, 55)}ms reads redirected to parity"
            )

    for start, end, kind in events:
        if not start <= ts < end or rng.random() > 0.08:
            continue

        if kind == "backup_job":
            lines.append(
                f"scsi[1190]: INFO  sequential read stream vol=vm-backup-{rng.randint(1, 40):02d} "
                f"initiator=iqn.2020-01.com.veeam:proxy-0{rng.randint(1, 4)} io_kb=1024"
            )
        elif kind == "vm_migration":
            lines.append(
                f"iscsi[1187]: INFO  new session initiator=iqn.1998-01.com.vmware:esx-new-{rng.randint(1, 9)} "
                f"svmotion write burst vol={vol}"
            )
        else:
            lines.append(
                f"scsi[1190]: INFO  large sequential write vol=etl-stage-{rng.randint(1, 8)} "
                f"initiator=iqn.2019-05.com.informatica:batch-01 io_kb=512"
            )

    return lines


def _event_boundaries(
    a: Array, start: datetime, end: datetime, events: list
) -> list[tuple[datetime, str]]:
    """Management-plane lines for workload changes that begin inside the window."""
    out = []
    for ev_start, _, kind in events:
        if start <= ev_start < end:
            what = {
                "backup_job": "job 'nightly-vm-backup' started by veeam-proxy-01",
                "vm_migration": "host group 'esx-cluster-new' mapped to 14 volumes",
                "batch_load": "schedule 'quarter-close-etl' started by informatica-svc",
            }[kind]
            out.append((ev_start, f"mgmt[902]: INFO  audit {what}"))

    return out


def logs(a: Array, end: datetime, hours: int) -> str:
    """The array's controller log for `hours` ending at `end`, as one text bundle."""
    start = end - timedelta(hours=hours)
    rng = _rng("logs", a.serial, start.isoformat(), hours)
    events = _benign_events(a.serial)
    extra = _event_boundaries(a, start, end, events)
    header = (
        f"# log bundle {a.serial} {a.model} firmware {a.firmware}\n"
        f"# window {_iso(start)} .. {_iso(end)}  controllers={a.controllers}\n"
        "# format: <timestamp> <serial> <controller> <subsystem>[pid]: <LEVEL> <message>\n"
    )
    lines = [header]
    ts = start
    ctrls = [f"ctrl{chr(65 + i)}" for i in range(a.controllers)]
    while ts < end:
        ts += timedelta(milliseconds=rng.randint(1500, 7500))
        while extra and extra[0][0] <= ts:
            lines.append(f"{_iso(extra[0][0])} {a.serial} ctrlA {extra.pop(0)[1]}\n")

        ctrl = rng.choice(ctrls)
        for text in [_noise(rng, a, ctrl), *_evidence(rng, a, ts, ctrl, events)]:
            lines.append(f"{_iso(ts)} {a.serial} {ctrl} {text}\n")

    return "".join(lines)


# --------------------------------------------------------------------------- #
# Firmware history                                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Commit:
    """One change in a firmware release."""

    sha: str
    version: str
    component: str
    title: str
    author: str
    diff: str


_GC_DIFF = """\
--- a/src/gc/reclaim_budget.c
+++ b/src/gc/reclaim_budget.c
@@ -71,9 +71,10 @@
 #define GC_BUDGET_MAX_MBPS      1600
-#define GC_LOW_WATERMARK        0.08   /* start aggressive reclaim */
+#define GC_EARLY_WATERMARK      0.12   /* start aggressive reclaim earlier, ahead of the cliff */
 #define GC_HEADROOM_FLOOR_MBPS  120
@@ -88,14 +89,12 @@ static uint32_t reclaim_budget_mbps(const struct seg_stats *s,
                                     const struct fg_load *fg)
 {
-    if (s->free_ratio < GC_LOW_WATERMARK) {
-        /* Out of space soon: reclaim hard, but never starve foreground writes. */
-        return MIN(GC_BUDGET_MAX_MBPS, fg_headroom_mbps(fg));
-    }
+    if (s->free_ratio < GC_EARLY_WATERMARK) {
+        /* Reclaim ahead of the cliff so a burst never finds the array full. */
+        return GC_BUDGET_MAX_MBPS;
+    }
     return clamp(fg_headroom_mbps(fg) / 2, GC_HEADROOM_FLOOR_MBPS, GC_BUDGET_MAX_MBPS);
 }
@@ -131,7 +130,7 @@ void gc_schedule_cycle(struct gc_ctx *ctx)
     budget = reclaim_budget_mbps(&ctx->seg, &ctx->fg);
-    ctx->yield_to_fg = budget < GC_BUDGET_MAX_MBPS;
+    ctx->yield_to_fg = budget < GC_BUDGET_MAX_MBPS;   /* false whenever budget is max */
     trace_gc_cycle(ctx, budget);
"""

_DEDUPE_DIFF = """\
--- a/src/dedupe/fp_index_cache.c
+++ b/src/dedupe/fp_index_cache.c
@@ -40,8 +40,12 @@
-/* Fingerprint index cache: fraction of controller DRAM. Sized for ddr up to 8:1. */
-#define FP_INDEX_CACHE_PCT      18
+/* Fingerprint index cache: fraction of controller DRAM.
+ * Reduced to fund NVMe metadata reservation (NVMEOF-2211). Profiled on the
+ * standard perf suite (ddr 2.5:1), no regression observed. */
+#define FP_INDEX_CACHE_PCT      11
+#define NVME_META_RESERVE_PCT   7
@@ -102,6 +106,7 @@ int fp_index_cache_init(struct fpidx *idx, size_t dram_bytes)
     idx->budget = dram_bytes * FP_INDEX_CACHE_PCT / 100;
+    mem_reserve(MEM_NVME_META, dram_bytes * NVME_META_RESERVE_PCT / 100);
     return fpidx_alloc_slabs(idx);
"""

_SNAPSYNC_DIFF = """\
--- a/src/replication/snapsync_sched.c
+++ b/src/replication/snapsync_sched.c
@@ -57,11 +57,13 @@ static time_t next_run(const struct snap_schedule *s, time_t now)
-    return now + s->period - (now % s->period) + s->jitter_s;
+    /* Align every schedule to the top of the period so partners see
+     * consistent recovery points across volumes (STOR-45120). */
+    time_t aligned = now + s->period - (now % s->period);
+    return aligned;                       /* jitter removed: breaks alignment */
 }
"""

_SNAPSYNC_FIX_DIFF = """\
--- a/src/replication/snapsync_sched.c
+++ b/src/replication/snapsync_sched.c
@@ -57,13 +57,14 @@ static time_t next_run(const struct snap_schedule *s, time_t now)
     time_t aligned = now + s->period - (now % s->period);
-    return aligned;                       /* jitter removed: breaks alignment */
+    /* STOR-47102: stagger freezes within a 5 min window by volume hash, so
+     * large schedules do not freeze every volume in the same second. */
+    return aligned + (s->vol_hash % 300);
 }
"""

_AUTHORS = (
    "r.iyer",
    "m.chen",
    "j.okafor",
    "s.lindqvist",
    "a.moreau",
    "p.nair",
    "k.tanaka",
    "d.alvarez",
)

# (version it shipped in, sha, component, title, diff or None for a generated one)
_CHANGES: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("6.1.1.200", "c1a77e0", "mgmt", "mgmt: paginate /v1/volumes responses over 500 rows", None),
    ("6.1.1.200", "4d2e9b1", "iscsi", "iscsi: honour DataPDUInOrder=No from initiators", None),
    (
        "6.1.1.200",
        "e92b5d0",
        "replication",
        "snapsync: align replication schedules to period boundary",
        _SNAPSYNC_DIFF,
    ),
    ("6.1.1.200", "0f6c3a8", "hw", "hw: add fan curve for 6090 chassis rev C", None),
    ("6.1.1.200", "b83d114", "cache", "cache: log nvram flush latency histogram", None),
    ("6.1.1.200", "9ae4f07", "raid", "raid: lower scrub priority during rebuild", None),
    ("6.1.1.200", "5c19d2e", "mgmt", "mgmt: GreenLake collector retries with backoff", None),
    (
        "6.1.2.100",
        "a41f9c2",
        "gc",
        "gc: begin aggressive reclaim at 12% free instead of 8%",
        _GC_DIFF,
    ),
    (
        "6.1.2.100",
        "7c03e18",
        "dedupe",
        "dedupe: shrink fingerprint index cache to fund NVMe metadata reservation",
        _DEDUPE_DIFF,
    ),
    (
        "6.1.2.100",
        "3b8d1f4",
        "replication",
        "snapsync: stagger aligned schedule starts by volume hash (STOR-47102)",
        _SNAPSYNC_FIX_DIFF,
    ),
    (
        "6.1.2.100",
        "e5071aa",
        "nvme",
        "nvme-of: add TCP transport for host connectivity (NVMEOF-2211)",
        None,
    ),
    ("6.1.2.100", "2c8f6b9", "fc", "fc: faster fabric relogin after RSCN", None),
    ("6.1.2.100", "71dd0c3", "gc", "gc: export reclaim budget in trace_gc_cycle", None),
    ("6.1.2.100", "d40a9e2", "mgmt", "mgmt: new audit events for host group mapping", None),
    (
        "6.1.2.100",
        "86b2f15",
        "cache",
        "cache: prefetch tuning for sequential reads over 256k",
        None,
    ),
    ("6.1.2.100", "f3e61c7", "hw", "hw: SMART wear threshold alert at 90%", None),
    ("6.1.2.200", "19c7a4d", "mgmt", "mgmt: fix audit log timezone on DST change", None),
    ("6.1.2.200", "c6e2b58", "nvme", "nvme-of: reconnect storm backoff", None),
    (
        "6.1.2.200",
        "8f0d3e1",
        "raid",
        "raid: faster degraded-read path for single slow member",
        None,
    ),
    ("6.1.2.200", "a90b7f6", "iscsi", "iscsi: reject zero-length immediate data", None),
    ("6.1.2.200", "44e1c0b", "gc", "gc: rename GC_LOW_WATERMARK references in comments", None),
)


def _generated_diff(sha: str, component: str, title: str) -> str:
    """A small, plausible diff for a change that is not part of any planted story."""
    rng = _rng("diff", sha)
    path = f"src/{component}/{title.split(': ', 1)[-1].split()[0].lower()}_{rng.randint(1, 9)}.c"
    line = rng.randint(40, 400)
    return (
        f"--- a/{path}\n+++ b/{path}\n@@ -{line},6 +{line},8 @@\n"
        f"     /* {title} */\n"
        f"-    rc = {component}_apply(ctx, cfg);\n"
        f"+    rc = {component}_apply(ctx, cfg);\n"
        f"+    if (rc == -EAGAIN)\n"
        f"+        rc = {component}_retry(ctx, cfg, {rng.randint(2, 8)});\n"
        "     return rc;\n"
    )


@cache
def commits() -> tuple[Commit, ...]:
    """Every change across the releases the fleet runs, oldest first."""
    rng = _rng("authors")
    return tuple(
        Commit(
            sha=sha,
            version=version,
            component=component,
            title=title,
            author=rng.choice(_AUTHORS),
            diff=diff or _generated_diff(sha, component, title),
        )
        for version, sha, component, title, diff in _CHANGES
    )


def changes_between(from_version: str, to_version: str) -> list[Commit]:
    """Commits that shipped after `from_version`, up to and including `to_version`."""
    lo, hi = FIRMWARE.index(from_version), FIRMWARE.index(to_version)
    shipped = set(FIRMWARE[lo + 1 : hi + 1])
    # Ordered as the release notes list them (by version, then sha), so a planted change
    # sits among its neighbours instead of heading the list.
    return sorted((c for c in commits() if c.version in shipped), key=lambda c: (c.version, c.sha))


def get_commit(sha: str) -> Commit | None:
    """Look a commit up by (a prefix of) its sha."""
    wanted = sha.strip().lower()
    return next((c for c in commits() if wanted and c.sha.startswith(wanted)), None)


# --------------------------------------------------------------------------- #
# Issue tracker                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Issue:
    """One engineering issue in the tracker."""

    key: str
    title: str
    status: str
    component: str
    affected_versions: tuple[str, ...]
    fixed_in: str | None
    description: str
    workaround: str | None
    linked_arrays: int


ISSUES: tuple[Issue, ...] = (
    Issue(
        "STOR-48213",
        "p99 write latency regression on 6.1.2.x under high capacity utilization",
        "Open - needs root cause",
        "gc",
        ("6.1.2.100", "6.1.2.200"),
        None,
        "Three customer escalations report write latency spikes during business hours after "
        "upgrading to 6.1.2.100. All three arrays are above 80% full. Perf lab could not "
        "reproduce on a 60% full array. Root cause unknown; suspected contention between "
        "foreground writes and a background process.",
        None,
        3,
    ),
    Issue(
        "STOR-47102",
        "Top-of-hour write stalls on arrays with many replicated volumes",
        "Fixed",
        "replication",
        ("6.1.1.200",),
        "6.1.2.100",
        "Replication schedules aligned to the period boundary freeze every scheduled volume "
        "at the same second. Arrays with more than ~45 replicated volumes see write ack "
        "stalls of 30 to 70 ms at :00.",
        "Stagger replication schedules manually by a few minutes per volume collection, "
        "or upgrade to 6.1.2.100.",
        11,
    ),
    Issue(
        "STOR-46550",
        "Read latency elevated for 10 minutes after controller failover",
        "Closed - expected behaviour",
        "cache",
        ("6.1.1.100", "6.1.1.200"),
        None,
        "Read cache is cold on the surviving controller after failover; latency returns to "
        "baseline once the working set is reloaded.",
        None,
        6,
    ),
    Issue(
        "STOR-46912",
        "GreenLake collector reports stale capacity after volume delete",
        "Fixed",
        "mgmt",
        ("6.1.1.100",),
        "6.1.1.200",
        "Capacity telemetry lags by up to 6 hours after large deletes.",
        None,
        4,
    ),
    Issue(
        "STOR-47788",
        "iSCSI session drops with DataPDUInOrder=No initiators",
        "Fixed",
        "iscsi",
        ("6.1.1.100",),
        "6.1.1.200",
        "Some Linux initiators negotiate out-of-order data PDUs; sessions reset under load.",
        None,
        2,
    ),
    Issue(
        "STOR-48002",
        "NVMe-oF TCP hosts reconnect storm after switch reboot",
        "Fixed",
        "nvme",
        ("6.1.2.100",),
        "6.1.2.200",
        "Hosts reconnect simultaneously and exhaust admin queue slots.",
        None,
        5,
    ),
    Issue(
        "STOR-47340",
        "Garbage collection throughput low on nearly empty arrays",
        "Closed - won't fix",
        "gc",
        ("6.1.1.200",),
        None,
        "Reclaim runs at the headroom floor when the array is under 30% full. Expected.",
        None,
        1,
    ),
    Issue(
        "STOR-48127",
        "Dedupe ratio reported incorrectly in GreenLake for thin clones",
        "Open",
        "dedupe",
        ("6.1.2.100",),
        None,
        "Display-only: ratio double-counts shared blocks of thin clones.",
        None,
        2,
    ),
    Issue(
        "STOR-47901",
        "Scrub competes with rebuild on 48-drive shelves",
        "Fixed",
        "raid",
        ("6.1.1.100",),
        "6.1.1.200",
        "Scrub priority not lowered during rebuild; rebuild takes 2x longer.",
        None,
        3,
    ),
    Issue(
        "STOR-48240",
        "Audit log timestamps off by one hour after DST change",
        "Fixed",
        "mgmt",
        ("6.1.2.100",),
        "6.1.2.200",
        "Cosmetic.",
        None,
        9,
    ),
    Issue(
        "STOR-46233",
        "SSD model S6X wear-out earlier than rated on write-heavy workloads",
        "Open - vendor engaged",
        "hardware",
        ("6.1.1.100", "6.1.1.200", "6.1.2.100"),
        None,
        "Field returns show a small batch of S6X drives reaching 90% wear early. Proactive "
        "replacement advised when SMART wear exceeds 90% with rising corrected media errors.",
        "Open a support case for proactive drive replacement.",
        7,
    ),
    Issue(
        "STOR-47655",
        "Backup windows cause latency alerts on shared arrays",
        "Closed - not a defect",
        "none",
        (),
        None,
        "Nightly backup sequential reads raise read latency on mixed-use arrays. Detector "
        "tuning ticket filed against AIOps (AIOPS-311).",
        None,
        18,
    ),
    Issue(
        "STOR-48301",
        "Storage vMotion bursts trigger write latency anomaly detector",
        "Closed - not a defect",
        "none",
        (),
        None,
        "Large VM migrations produce write bursts; latency returns to baseline after the "
        "migration completes.",
        None,
        12,
    ),
)


def get_issue(key: str) -> Issue | None:
    """Look an issue up by key, case-insensitively."""
    wanted = key.strip().upper()
    return next((i for i in ISSUES if i.key == wanted), None)


def search_issues(query: str, component: str | None = None) -> list[Issue]:
    """Rank tracker issues by word overlap with `query`, optionally within a component."""
    words = {w for w in query.lower().replace("-", " ").split() if len(w) > 2}
    scored = []
    for issue in ISSUES:
        if component and issue.component != component.lower():
            continue

        text = f"{issue.title} {issue.description} {issue.component} {' '.join(issue.affected_versions)}".lower()
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, issue))

    scored.sort(key=lambda pair: -pair[0])
    return [issue for _, issue in scored[:6]]


# --------------------------------------------------------------------------- #
# Fleet cohort queries                                                          #
# --------------------------------------------------------------------------- #


def query_fleet(
    firmware: str | None = None,
    workload: str | None = None,
    min_capacity_pct: float | None = None,
    dedupe_enabled: bool | None = None,
    min_replicated_volumes: int | None = None,
) -> dict[str, Any]:
    """How many arrays match a cohort, and how many of them raised latency signals this week."""

    def matches(a: Array) -> bool:
        return (
            (firmware is None or a.firmware == firmware)
            and (workload is None or a.workload.lower() == workload.lower())
            and (min_capacity_pct is None or a.capacity_used_pct >= min_capacity_pct)
            and (dedupe_enabled is None or a.dedupe_enabled == dedupe_enabled)
            and (min_replicated_volumes is None or a.replicated_volumes >= min_replicated_volumes)
        )

    cohort = [a for a in arrays() if matches(a)]
    serials = {a.serial for a in cohort}
    by_metric: dict[str, set[str]] = {}
    for s in signals():
        if s.serial in serials:
            by_metric.setdefault(s.metric, set()).add(s.serial)

    by_firmware: dict[str, int] = {}
    for a in cohort:
        by_firmware[a.firmware] = by_firmware.get(a.firmware, 0) + 1

    return {
        "arrays": len(cohort),
        "arrays_with_signal_7d": {
            metric: len(found) for metric, found in sorted(by_metric.items())
        },
        "by_firmware": dict(sorted(by_firmware.items())),
        "sample_serials": [a.serial for a in cohort[:8]],
    }


# --------------------------------------------------------------------------- #
# Filed investigations                                                          #
# --------------------------------------------------------------------------- #


def record_filing(filing: dict[str, Any]) -> dict[str, Any]:
    """Append one investigation handoff to the filings log and return it with its id."""
    FILINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = filings()
    record = {
        "investigation_id": f"INV-{7001 + len(existing)}",
        "filed_at": datetime.now(UTC).isoformat(),
        **filing,
    }
    with FILINGS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return record


def filings() -> list[dict[str, Any]]:
    """Every investigation filed so far, oldest first."""
    if not FILINGS_PATH.exists():
        return []

    with FILINGS_PATH.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def open_signals() -> list[Signal]:
    """Signals nobody has filed an investigation for yet, newest first."""
    done = {f.get("signal_id") for f in filings()}
    return [s for s in signals() if s.signal_id not in done]
