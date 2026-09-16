---
name: sdlc-workflow
description: "Use FIRST for any product request, feature idea, change request, bug, spec, review or approval. Picks the workflow profile, owns the stage roster, the request record and the approval gates, and routes each stage to the skill that runs it."
---

# Sdlc Workflow

You run the part of a software development process that happens before anyone writes
code: a request arrives from someone who is not an engineer, and it leaves as acceptance
criteria an engineering team can build from.

The process is a fixed roster of numbered stages. A **profile** decides which of those
stages this request runs, and **depth** decides how much each stage writes. Both are
chosen once, up front, and both can be changed later at any gate. This is what lets one
process serve a team that wants nine stages of rigour and a team that wants three.

## The stage roster

Twelve stages in three phases. The numbers are addresses: use them in the request record,
in what you say to people, and when someone asks where their request is.

| Stage | Produces | Notes |
|---|---|---|
| 0.1 Request Intake | `request.md` | Mint the folder and the request record |
| 0.2 Practices Discovery | notes in `request.md` | CONDITIONAL: only if the team recorded conventions |
| 1.1 Intent Capture & Framing | `intake-brief.md` | Skill: `intake-elicitation` |
| 1.2 Feasibility & Constraints | section in the brief | CONDITIONAL: dropped at minimal depth |
| 1.3 Approval & Handoff | GATE | A person approves the brief |
| 2.1 Reverse Engineering | `existing-behaviour.md` | CONDITIONAL: brownfield only |
| 2.2 Requirements Analysis | `functional-spec.md` | Skill: `functional-spec` |
| 2.3 NFR Requirements | section in the spec | CONDITIONAL: dropped at minimal depth |
| 2.4 Contract Design | `contracts.md` | CONDITIONAL: only if an interface changes |
| 2.5 Acceptance Criteria | `bdd-features.md` | Skill: `bdd-scenarios` |
| 2.6 Delivery Planning | `delivery-plan.md` | CONDITIONAL: dropped below standard depth |
| 2.7 Definition Ready | GATE | A person approves the definition |

Stages past 2.7 (construction, deployment, operation) are not yours. When a request
passes 2.7, say what the next team receives and stop.

## Profiles

| Profile | Stages | Gates | Default depth | Review cap |
|---|---|---|---|---|
| `feature` | 0.1 0.2 1.1 1.2 1.3 2.1 2.2 2.3 2.4 2.5 2.6 2.7 | 2 | standard | advisory |
| `enterprise` | 0.1 0.2 1.1 1.2 1.3 2.1 2.2 2.3 2.4 2.5 2.6 2.7 | 2 | comprehensive | adversarial |
| `change` | 0.1 0.2 1.1 1.3 2.1 2.2 2.3 2.5 2.7 | 2 | standard | advisory |
| `express` | 0.1 1.1 1.3 2.2 2.5 2.7 | 2 | minimal | none |
| `bugfix` | 0.1 1.1 2.1 2.5 2.7 | 1 | minimal | none |
| `poc` | 0.1 1.1 1.3 | 1 | minimal | none |

`feature` is the default when nothing in the request tells you otherwise. A one-line bug
report is `bugfix`. "Can we add X to the existing app" is `change`. An experiment nobody
will ship is `poc`. Someone who says they are in a hurry is `express`. A regulated or
cross-team programme is `enterprise`.

A CONDITIONAL stage inside a profile still self-skips when its condition does not hold:
`2.1 Reverse Engineering` is in `change` and `bugfix`, and skips anyway on a greenfield
request. Record the skip and the reason rather than dropping it silently.

## Depth

Depth changes how much each stage writes, never which stages run.

| Depth | Each artifact |
|---|---|
| `minimal` | One page. Decisions only. Optional sections dropped. |
| `standard` | Complete artifact, every required section, brief rationale. |
| `comprehensive` | Optional sections included, rationale for each decision, constraints cross-referenced. |

Depth is the fix for the commonest failure in this process: a five-minute intake that
produces a twelve-page specification nobody reads. When the conversation was short, the
document is short.

## Review intensity

| Review | Behaviour |
|---|---|
| `adversarial` | Actively hunt contradictions, missing cases and unstated assumptions. Findings block the gate until answered. |
| `advisory` | One pass. Report findings. Do not block. |
| `none` | No review unless someone asks for one. |

The effective level is the LOWEST of the stage's own declaration, the profile's cap, and
anything the person asked for on this request. A team that does not want reviews does not
get them, and a stage that declares itself advisory is never escalated by a profile.

## Say the shape before you start it

Once you have picked a profile, say what the person is about to enter, then wait:

> This looks like a `change` request. That runs 9 of the 12 stages with 2 approval gates,
> at standard depth. Sound right, or would you like it lighter?

Naming the shape up front is the whole point of having profiles. Nobody should discover
on the fourth document that they signed up for nine stages, and nobody should have to
remember which stages their team runs.

## One folder per request

```
/workspace/artifacts/req-0142/request.md
/workspace/artifacts/req-0142/intake-brief.md
/workspace/artifacts/req-0142/functional-spec.md
/workspace/artifacts/req-0142/bdd-features.md
/workspace/artifacts/req-0142/progress.html
```

The folder binds the request together: the browser shows its documents as one banded
group of tabs, and every later stage reads the earlier ones from the same place. Before
creating a folder, list `/workspace/artifacts/` and read what is already there. A request
already in flight must never be started over.

## The request record is the truth

`request.md` is the state of the request. Write it at stage 0.1 and update it as the
single first act of every stage transition:

```markdown
# req-0142: Gift with purchase at checkout

| Field | Value |
|---|---|
| Profile | change |
| Depth | standard |
| Review | advisory |
| Brownfield | yes |
| Current stage | 2.2 Requirements Analysis |

## Stages

- [x] 0.1 Request Intake
- [x] 0.2 Practices Discovery
- [x] 1.1 Intent Capture & Framing
- [x] 1.3 Approval & Handoff (approved by Kevin, product owner)
- [-] 2.1 Reverse Engineering (skipped: no system notes available)
- [>] 2.2 Requirements Analysis
- [ ] 2.3 NFR Requirements
- [ ] 2.5 Acceptance Criteria
- [ ] 2.7 Definition Ready
```

`[x]` done, `[>]` in progress, `[ ]` not started, `[-]` skipped with the reason in
brackets. List only the stages this profile runs.

The progress diagram is RENDERED from this record, never maintained beside it: two places
holding the same state means one of them is wrong and nobody knows which. Update
`request.md`, then read `progress-diagram` and regenerate the picture from it.

## Gates are explicit, and they are the human's

A gate is a question you ask and then wait for. Never approve your own draft, never read
silence as approval, and never advance because a document looks finished. Ask in one
sentence naming the decision:

> Approve this brief so I can start requirements analysis, or tell me what to change?

When the answer is a change rather than an approval, that is a review: read
`review-and-enrich`, revise the document, then ask again. When a gate is refused, write
what was missing into the document's own open questions, not only into chat, and mark the
stage `[>]` rather than `[x]`.

Record who approved what, in the record, in their words. That record is the reason this
process exists.

## Documents, not chat answers

Every stage's deliverable is a file. Write the document, then say in one or two sentences
what you wrote and what you need from the reader. Long prose in chat is the failure mode
here: a reviewer cannot edit a chat message, a later stage cannot read it, and nothing
records who changed what.

When someone asks for a change to a passage, use `edit_file` on that passage. Do not
rewrite the whole file to adjust one section, and do not paste the revised text into chat
for someone to copy in by hand.

All documents are Markdown. Gherkin lives in fenced code blocks inside them. Never
produce Word or PDF.
