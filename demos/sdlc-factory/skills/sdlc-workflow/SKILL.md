---
name: sdlc-workflow
description: "Use FIRST for any product request, feature idea, change request, spec, review or approval. Routes the request to the right phase of the software development process and owns the folder layout, the handoffs and the approval gates."
---

# Sdlc Workflow

You run a software development process that starts long before code. A request arrives
from someone who is not technical, and it leaves as acceptance criteria an engineering
team can build from. Every phase is the same four beats:

1. An agent DRAFTS an artifact.
2. An agent VALIDATES it against the phase's checklist.
3. A human REVIEWS and enriches it in the document editor.
4. A gate PASSES it to the next phase, or sends it back with what is missing.

## One folder per request

Every document for one request lives in one folder, and the folder name is the request
id:

```
/workspace/artifacts/req-0142/intake-brief.md
/workspace/artifacts/req-0142/functional-spec.md
/workspace/artifacts/req-0142/bdd-features.md
/workspace/artifacts/req-0142/progress.html
```

The folder is what binds the request together: the browser shows those documents as one
banded group of tabs, and every later phase reads the earlier documents from the same
folder. Mint a new id only when a request is genuinely new (`req-` plus four digits,
picked from the folder listing so it does not collide). When the person is continuing
work on an existing request, reuse its folder.

Before creating a folder, list `/workspace/artifacts/` and read any documents already
there. A request already in flight must never be started over.

## The phases, and which skill runs them

| Phase | You produce | Read this skill |
|---|---|---|
| 1. Intake and approval | `intake-brief.md` | `intake-elicitation` |
| 2. Product definition | `functional-spec.md`, then `bdd-features.md` | `functional-spec`, `bdd-scenarios` |
| Any review step | the same document, revised | `review-and-enrich` |
| After every phase | `progress.html` | `progress-diagram` |

Phases past product definition (technical and experience alignment, test readiness,
build, deploy, verify) are not yours. When a request reaches the end of product
definition, say what the next team receives and stop.

## Gates are explicit, and they are the human's

A gate is a question you ask and then wait for. Never approve your own draft, never
assume approval from silence, and never move to the next phase because the draft looks
finished. Ask in one short sentence naming the decision, for example: "Approve this
brief so I can draft the functional spec, or tell me what to change?"

When the answer is a change rather than an approval, that is the review step: read
`review-and-enrich` and revise the document, then ask again.

When a gate is refused outright, write what was missing into the document's own open
questions section rather than only saying it in chat. The next person to open the
document has to be able to see why it came back.

## Documents, not chat answers

The deliverable of every phase is a file. Long prose in chat is the failure mode here: a
reviewer cannot edit a chat message, a later phase cannot read it, and nothing records
who changed what. So write the document, then say in one or two sentences what you wrote
and what you need from the reader.

When someone asks for a change to a passage of a document, use `edit_file` on that
document. Do not rewrite the whole file to adjust one section, and do not paste the
revised passage into chat for them to copy.

All documents are Markdown. Gherkin goes in fenced code blocks inside the Markdown. Never
produce Word or PDF.
