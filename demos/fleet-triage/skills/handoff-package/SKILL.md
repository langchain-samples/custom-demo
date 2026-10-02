---
name: handoff-package
description: "Use when writing the engineer-ready handoff for a finished fleet investigation, before calling file_investigation. The section template engineering expects and what each section must contain."
---

# Handoff package

The engineer who picks this up has five minutes and has never seen the signal. They
decide from the first screen whether to act, and they check the evidence before they
trust the conclusion. Write for that person.

Save it as `/workspace/artifacts/<SIGNAL_ID>-handoff.md`, and pass the same text to
`file_investigation` as `handoff_markdown`.

## Template

```markdown
# <SIGNAL_ID>: <one-line finding, e.g. "GC starves foreground writes on 6.1.2.100 above 88% full">

| | |
|---|---|
| Verdict | new defect / duplicate of <KEY> / hardware fault / not a defect |
| Recommendation | <one of the five> |
| Confidence | low / medium / high, and the one thing that would raise it |
| Array | <serial>, <model>, <customer> (<site>) |
| Firmware | <current> since <date>, upgraded from <previous> |
| Component | <component> |
| Suspected commit | <sha> "<title>", or none |
| Duplicate of | <KEY> (<status>), or new |

## What the detector saw
Metric, observed against baseline, window, and whether it recurs. Figures only.

## Root cause
The mechanism in two to four sentences: which code path, what condition triggers it,
and why this array meets that condition.

## Evidence
- **Telemetry:** the figures that moved together, inside and outside the window.
- **Logs:** counts, plus up to five verbatim lines in a code block.
- **Code:** the diff lines that produce the behaviour, in a code block.
- **Fleet cohort:** "N of M arrays with <attribute> raised <metric> signals, against
  n of m without."

## Ruled out
Each hypothesis that was refuted, with the one fact that refuted it.

## Recommendation
The action, who takes it, and what the customer can do meanwhile.

## Artifacts
Every file in `/workspace/investigations/<SIGNAL_ID>/`, one per line.
```

## Rules

- No section may be empty. When one does not apply (no commit for a hardware fault),
  say so in a line.
- Never quote a figure the sandbox did not compute, and never quote a log line that is
  not in the bundle.
- A dismissed signal still gets the full package: the engineer needs to see why it was
  safe to close, and the detector team uses dismissals to tune thresholds.
