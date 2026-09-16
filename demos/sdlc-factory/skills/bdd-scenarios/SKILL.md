---
name: bdd-scenarios
description: "Use after a functional spec is reviewed, to write the BDD features and scenarios in Gherkin syntax that engineering and QA build and test from."
---

# Bdd Scenarios

This is the last document product definition produces, and the one everything downstream
reads. Engineering builds from it, QA tests from it, and the build agents in later phases
react far better to it than to prose user stories, because a scenario names its
preconditions, its trigger and its observable outcome with no room to interpret.

Read `functional-spec.md` from the request folder. Every acceptance criterion in it
becomes at least one scenario here.

## Gherkin, properly

```gherkin
Feature: <capability from the spec>

  As a <role>
  I want <capability>
  So that <outcome from the brief>

  Background:
    Given <the state every scenario in this feature shares>

  Scenario: <the case, named as an outcome and not as a test>
    Given <precondition>
    And <another precondition>
    When <the single triggering action>
    Then <the observable result>
    And <any further observable result>
```

Rules that make the difference between usable Gherkin and decoration:

- **One trigger per scenario.** Two `When` steps mean two scenarios.
- **`Then` is observable.** Something a person or a test can see. Never "the system
  processes it correctly".
- **No UI mechanics.** "When the consultant applies the offer", not "When the user clicks
  the blue Apply button". The spec is about behaviour, and a button that moves must not
  invalidate the scenario.
- **Name the negative cases.** Every capability needs the scenarios where it is refused,
  expired, out of stock, over a limit, or already used. A feature with only happy paths
  is the single most common defect in this document.
- **Use a `Scenario Outline` with an `Examples` table** when the same behaviour varies
  only by value (thresholds, tiers, dates). One outline beats six near-identical
  scenarios.
- **Every term comes from the spec.** If a word is not in the brief or the spec, it is
  not in a scenario.

## What to write

Write `/workspace/artifacts/<request-id>/bdd-features.md`: a short heading, a
traceability line, then one fenced `gherkin` block per feature.

```markdown
# Acceptance criteria: <request title>

**Request id:** req-0142
**Source:** functional-spec.md
**Status:** Draft for review

Each scenario below traces to a numbered acceptance criterion in the functional spec.

## Feature: <name>

Traces to: AC 1, AC 2

```gherkin
Feature: ...
```
```

Close with a coverage note: which acceptance criteria are covered, and any that are not
yet, with the reason.

## Then hand it on

Update the progress diagram (read `progress-diagram`), then ask for review. Once the
scenarios are agreed, product definition is complete: say that the next phase is
technical and experience alignment, name the documents the architecture and design teams
will receive, and stop.
