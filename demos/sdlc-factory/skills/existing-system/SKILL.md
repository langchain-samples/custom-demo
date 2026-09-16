---
name: existing-system
description: "Use when a request changes something that already exists, or before drafting for a team whose conventions are recorded. Runs stage 0.2 Practices Discovery and stage 2.1 Reverse Engineering, and self-skips when there is nothing to read."
---

# Existing System

Two conditional stages live here, and both answer the same question: what is already
true, before anyone writes a specification about changing it.

Almost every request that reaches this process is a change to a system that exists.
Drafting a specification for one as though it were new is how a spec ends up describing a
product nobody has, contradicting behaviour customers already rely on.

## Self-skip, out loud

Both stages are CONDITIONAL. When the condition does not hold, skip and RECORD the skip
with its reason in `request.md`:

```markdown
- [-] 2.1 Reverse Engineering (skipped: new capability, nothing to read)
```

Never skip silently, and never substitute a guess for the reading. "I could not find
notes on the current checkout flow" is a useful line in a brief. An invented description
of that flow is the worst thing this process can produce, because everything downstream
treats it as fact.

## 0.2 Practices Discovery

Runs when the team has recorded conventions: a practices note, a template, a previous
request folder from the same team. Look for them, and when you find them, note in
`request.md` which conventions you are following.

What to take: the words they use for their own roles and systems, the sections their past
specs carried, the acceptance-criteria style they write in, and anything they have said
they always or never do. Follow those over the defaults in these skills. A spec that
matches the team's existing documents gets reviewed; one that arrives in a foreign shape
gets rewritten.

When there is nothing recorded, skip. Do not interview someone about their conventions:
that is not what they came for, and stage 1.1 is already asking for their time.

## 2.1 Reverse Engineering

Runs on brownfield work: the request changes an existing capability. Produces
`existing-behaviour.md` in the request folder.

Read what is actually available to you: earlier request folders for the same area, specs
and criteria from previous changes, and any system notes or data files you have been
given. Then write what you found:

```markdown
# Existing behaviour: <the area this request changes>

**Request id:** req-0142
**Read:** the documents and files you actually opened, one per line

## How it works today

The current behaviour, in the terms the team uses for it.

## Where this request touches it

The specific points the change lands on.

## What I could not determine

Everything you looked for and did not find, one per line. Be specific: "no record of what
happens when an order is edited after submission" is actionable; "some details unclear"
is not.

## Risks in changing it

Behaviour that existing users depend on and that this request could disturb.
```

Keep it to the area the request touches. A survey of the whole system is not what this
stage is for and nobody will read it.

The "What I could not determine" section is the most valuable part of this document and
the one there is most temptation to leave thin. Every line in it is a question the
functional spec must either answer or carry forward as an open question, and a gap named
here is a gap that gets discussed instead of guessed at.

## Then hand it on

Mark the stage `[x]`, or `[-]` with its reason, in `request.md`. Set the current stage,
regenerate the diagram (read `progress-diagram`), and continue with the stage the profile
runs next. Neither of these stages has a gate: they inform the next document rather than
needing approval of their own.
