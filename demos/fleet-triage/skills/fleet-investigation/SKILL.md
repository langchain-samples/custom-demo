---
name: fleet-investigation
description: "Use FIRST for any fleet anomaly signal (a SIG- id, an array serial with a latency or I/O complaint, or 'investigate the next signal'). The step-by-step investigation procedure, the evidence file layout and the completeness checks."
---

# Fleet investigation

One investigation answers one signal. Work through the steps in order. Each step writes
its evidence to the investigation folder, so the handoff can cite files instead of
paraphrasing them, and so a subagent can analyse what you fetched.

## The investigation folder

`/workspace/investigations/<SIGNAL_ID>/`, created at step 1:

| File | Written at | Contents |
|---|---|---|
| `signal.json` | 1 | The signal record |
| `array.json` | 2 | The Flex array record |
| `telemetry.json` | 3 | Telemetry for the signal window |
| `logs.txt` | 4 | The controller log bundle |
| `log-findings.md` | 4 | What the log parse printed, with counts |
| `changes.json` | 5 | Commits between the previous and current firmware |
| `diff-<sha>.patch` | 5 | Diffs of the commits you shortlisted |
| `cohort.json` | 6 | The fleet cohort comparison |
| `hypotheses.md` | 7 | Each hypothesis and its verdict |

Save each tool result with `write_file` as you go. When a result was too large to come
back inline, the harness has already saved it to a file and told you the path: copy
that file into the folder with `execute` (`cp`), rather than re-fetching it.

## Step 1: Read the signal

`get_signal`. Note the array, the metric, the detection time, the duration and the
ratio of observed to baseline. The detector's confidence is low by design; it is a
reason to look, not evidence.

## Step 2: Identify the array and its firmware

`get_array`. Record the model, current firmware, previous firmware and when the upgrade
happened, capacity used, dedupe (on or off, and the ratio), replicated volume count and
workload. An upgrade in the days before the signal is the first thing to test.

## Step 3: Telemetry

`get_telemetry` for 24 hours ending one hour after detection, all metrics. A full day
comes back as a saved file, like the log bundle: copy it to `telemetry.json` and parse
it in the sandbox. Never retype tool output into a file. Establish, with numbers:

- Is the excursion confined to the detection window, or recurring (every afternoon,
  every hour at :00)?
- Which metrics moved together? Latency with IOPS or MB/s up points at a workload
  change. Latency with IOPS flat and CPU up points inside the array. Latency with
  cache hit rate down points at a cache.
- Is latency proportionate to load? Every array is busier in the afternoon. Compare
  latency per 1,000 IOPS at peak with the same figure overnight: a healthy array
  stays close, a defect under load does not.

## Step 4: Logs

`fetch_array_logs` for 2 hours ending 30 minutes after detection. The bundle is large:
never read it into the conversation. Parse it with Python in the sandbox:

- Count lines by subsystem and level, and list every `WARN` and `ERROR` message pattern
  with its count.
- For each subsystem that warns, pull 3 representative lines verbatim.
- Look for management-plane audit events (jobs started, hosts mapped) in the window.
- From `trace` io_path lines, compare the latency breakdown (`q_us`, `media_us`,
  `gc_wait_us`, `dedupe_us`) inside the anomaly against outside it.

Write what the code printed to `log-findings.md`.

## Step 5: What changed in the code

If the current firmware has a previous version, `list_firmware_changes(previous,
current)`. Shortlist the commits whose component matches what telemetry and logs point
at, and read each with `get_commit_diff`. A diff implicates a commit only when its
mechanism explains the evidence: say which line of the diff, and which log line or
metric it produces.

## Step 6: Is it the firmware, or this array?

`query_fleet` twice, varying one attribute: the suspect firmware against the release
before it, or the same firmware above and below a threshold the diff suggests (capacity
used, dedupe on, replicated volume count). Report both cohorts as "N of M arrays raised
a <metric> signal". A defect shows up across its cohort and not outside it.

## Step 7: Test the hypotheses in parallel

By now you have two to four live hypotheses (for example: workload change, a specific
commit, hardware, a known issue). Dispatch one subagent per hypothesis **in the same
turn**, so they run concurrently. Give each:

- the one hypothesis it owns, stated so it can be confirmed or refuted,
- the exact file paths in the investigation folder,
- the instruction to use `execute` with Python, and to return: CONFIRMED, REFUTED or
  INCONCLUSIVE, the counts and figures its code printed, and up to five verbatim log
  lines.

Record every verdict in `hypotheses.md`. Only a CONFIRMED hypothesis with a mechanism
can become the root cause.

## Step 8: Duplicates

`search_issues` with the symptom, component and version, and `get_issue` on anything
close. Decide: duplicate of an existing issue (say which, and whether its root cause is
known), or new. A fixed issue whose fix is in a release newer than the array's is a
reason to recommend that upgrade.

## Step 9: Recommend and file

Pick exactly one recommendation:

| Recommendation | When |
|---|---|
| `escalate_engineering` | A firmware defect with no fix yet, new or open |
| `upgrade_firmware` | A known issue fixed in a release newer than the array's |
| `apply_workaround` | A documented workaround exists and an upgrade is not appropriate |
| `replace_hardware` | A component is failing; firmware is not at fault |
| `dismiss` | A workload change or other expected behaviour explains the signal |

Read `/skills/handoff-package/SKILL.md`, write the package to
`/workspace/artifacts/<SIGNAL_ID>-handoff.md`, then call `file_investigation` with the
same content in `handoff_markdown` and the evidence file paths in `artifacts`. Whenever
the root cause names a commit, pass its sha as `suspected_commit`; whenever the
investigation names an existing issue, pass its key as `duplicate_of`. The engineering
queue routes and dedupes on those fields, not on the prose. File once: a signal that is
already filed cannot be filed again.

## Completeness check

Before your final reply, confirm each of these is true and fix any that is not:

1. The signal is explained: what moved, when and by how much, with figures.
2. The firmware version and upgrade date are stated.
3. The relevant code changes were reviewed, and the implicated commit (or why none is
   implicated) is stated.
4. The root cause names a mechanism and cites log lines, telemetry and code.
5. The evidence files exist in the investigation folder and are listed in the handoff.
6. The issue tracker was searched, and the duplicate or "new" call is stated.
7. There is exactly one recommendation, and it was filed.
