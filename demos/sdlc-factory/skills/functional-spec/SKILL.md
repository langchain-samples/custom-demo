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

## How much to write

Depth comes from `request.md`, and it decides the shape of what you produce:

| Depth | The spec |
|---|---|
| `minimal` | One page. Summary, In scope, Acceptance criteria, Open questions. Nothing else. |
| `standard` | Every section below, one short paragraph per behaviour. |
| `comprehensive` | Every section, rationale for each decision, and every constraint from the brief cross-referenced to the behaviour it limits. |

Two stages fold into this document when the profile runs them. **2.3 NFR Requirements**
adds a Non-functional requirements section: the load, latency, availability and privacy
expectations the request implies, and "None stated" where the brief is silent rather than
numbers you invented. It is dropped at minimal depth.

**2.4 Contract Design** is conditional and produces its own `contracts.md`: run it only
when an interface actually changes shape, and say in the spec that you did. A request
that only alters behaviour behind an existing interface skips it.

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

## Then hand it on

This is stage 2.2. Mark it `[x]` in `request.md`, mark 2.3 and 2.4 done or skipped with
their reason, set the current stage, and regenerate the diagram (read
`progress-diagram`).

Then ask the product manager to review and enrich the spec. When they ask for changes,
read `review-and-enrich` and edit this document in place. Only once they are satisfied do
you move on to `bdd-scenarios` for stage 2.5.
