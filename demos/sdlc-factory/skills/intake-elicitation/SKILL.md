---
name: intake-elicitation
description: "Use when someone arrives with a new request, feature idea or problem in one or two sentences. Asks the few questions that turn it into a written brief a product owner can approve."
---

# Intake Elicitation

Someone has arrived with one or two sentences and a real need. Your job is to turn that
into a brief in five to ten minutes of their time. Not a design session.

## How to ask

Ask your questions IN YOUR REPLY, as a sentence or two of plain text. Do not use
`ask_user` for elicitation: it offers a fixed set of choices and pauses the
conversation, and almost nothing you need here has a knowable set of answers. "What
happens today instead?" cannot be multiple choice. Reserve `ask_user` for a genuinely
narrow either-or that has come up mid-draft.

Ask two or three questions per message, never a numbered interrogation of eight. Stop as
soon as you can write the brief. How many you get is set by the request's depth, which
`sdlc-workflow` recorded in `request.md`:

| Depth | Questions | The brief |
|---|---|---|
| `minimal` | At most 3 | Half a page. What, why, and how we would know. |
| `standard` | At most 6 | A page, every section below. |
| `comprehensive` | At most 8 | Every section, plus the feasibility and constraints detail from stage 1.2. |

At minimal depth, stage 1.2 Feasibility & Constraints does not run: write "Not assessed at
this depth" under Constraints rather than pressing for answers the profile said to skip. If an answer is
vague and the vagueness does not change what gets built, let it go and note it as an open
question instead of pressing.

Ask about the things that change the outcome:

- **Who has the problem, and what do they do today instead?** The workaround tells you
  what the request is really worth.
- **What would be different afterwards?** In their words, not as a feature list.
- **Is this new, or a change to something that exists?** If it exists, which part.
- **Who else is affected?** Other teams, existing reports, anyone downstream.
- **Is anything fixed?** A date, a campaign, a regulation, a system it must work with.
- **How would you know it worked?** Their measure, however rough.

Never ask for a technical design, a data model, a screen layout or an effort estimate.
They came to you because filling in a form with those fields is what they cannot do.

## Use their words

Write in the requester's vocabulary. If they said "consultants", the brief says
consultants, not "end users". A brief that has translated everything into product
language is a brief they cannot confirm is correct.

## Confirm, then write

Before writing the file, play back a three or four sentence summary and ask whether you
have it right. This is the only confirmation step in the phase, and it is what makes the
brief theirs rather than yours.

Then write `/workspace/artifacts/<request-id>/intake-brief.md` with exactly these
sections:

```markdown
# <Short request title>

**Request id:** req-0142
**Raised by:** <name or role, as they gave it>
**Date:** <today>

## What is being asked for

Two or three sentences in the requester's own words.

## Why it matters

The problem behind the request, and what happens today without it.

## Who is affected

Roles, teams and anyone downstream.

## Constraints

Dates, systems, policies. Say "None given" rather than leaving it out.

## How we would know it worked

The requester's own measure.

## Open questions

Anything you deliberately did not press on, one per line. Say "None" if there are none.
```

Keep it to a page. The brief is the start of a conversation with a product owner, not a
specification.

## Then hand it to the gate

This is stage 1.1. Mark it `[x]` in `request.md`, set the current stage to the next one
the profile runs, regenerate the diagram (read `progress-diagram`), then ask the product
owner to approve the brief or say what to change.

That request is stage 1.3, and it is a gate. Do not start requirements analysis until a
person approves. On the `bugfix` profile there is no 1.3 at all: go straight to 2.1.
