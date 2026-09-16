---
name: functional-spec
description: "Use after a product owner approves an intake brief. Drafts the functional spec that turns an approved request into what the product will actually do."
---

# Functional Spec

The brief says what someone asked for. The functional spec says what the product will do
about it, in enough detail that an architect and a designer can work from it in
parallel.

Read `intake-brief.md` from the request folder first. Everything here builds on it, and a
spec that contradicts the brief is the defect this phase exists to prevent.

## Draft, do not invent

You will find gaps. A brief written in five minutes always has them. Two rules:

- Where a reasonable default exists, state it AS a default, in the spec, marked so a
  reviewer can see you chose it: "Assumed: the offer applies per order, not per item."
- Where no reasonable default exists, do not pick one. Put it in Open questions and carry
  on. A spec that quietly answers a question nobody asked is worse than one with a hole
  in it, because the hole gets discussed and the invention gets built.

Never invent a system name, a data source, a rule or a number that was not given to you.

## What to write

Write `/workspace/artifacts/<request-id>/functional-spec.md`:

```markdown
# Functional spec: <request title>

**Request id:** req-0142
**Source:** intake-brief.md
**Status:** Draft for product manager review

## Summary

What the product will do, in three or four sentences.

## In scope

Numbered capabilities. One line each, each one testable.

## Out of scope

What this deliberately does not do. Name the things a reader would otherwise assume.

## Behaviour

For each capability in scope, a short paragraph: the trigger, what happens, what the
person sees, and what happens when it cannot be done.

## Data and systems touched

Only what the brief and the conversation actually named.

## Assumptions

Defaults you chose, one per line, each one a reviewer could overturn.

## Open questions

Gaps you deliberately left, one per line, addressed to whoever can answer.

## Acceptance criteria

Plain-language criteria, numbered. These become Gherkin scenarios in the next step, so
make each one a single observable outcome.
```

## Then hand it to the gate

Update the progress diagram (read `progress-diagram`), then ask the product manager to
review and enrich the spec. When they ask for changes, read `review-and-enrich` and edit
this document in place. Only once they are satisfied do you move on to `bdd-scenarios`.
