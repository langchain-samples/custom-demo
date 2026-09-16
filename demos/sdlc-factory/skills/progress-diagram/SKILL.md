---
name: progress-diagram
description: "Use after finishing or advancing any phase, or when someone asks how far along a request is. Creates and maintains the progress diagram for a request folder."
---

# Progress Diagram

Every request folder carries a `progress.html` that draws the whole process and marks
where this request has got to. It opens as a tab beside the documents, so "how far along
is this?" is answered by looking rather than by asking.

Write it after the FIRST document of a request exists, and update it every time a phase
completes or a gate is passed. A diagram that is stale is worse than none, because
someone will act on it.

## What it says

Three states only:

- **done** - the phase finished and its gate was passed by a person.
- **current** - the phase being worked on now, including one waiting at its gate.
- **pending** - not started.

A phase is `done` only when a human approved it. A draft sitting at a gate is `current`.

## The file

Write `/workspace/artifacts/<request-id>/progress.html`, replacing the request id, the
title, the `class` lines and the status line:

```html
<!doctype html>
<meta charset="utf-8">
<title>req-0142 progress</title>
<style>
  body { margin: 0; padding: 24px; font: 14px system-ui, sans-serif; color: #16202c; background: #fff; }
  h1 { margin: 0 0 4px; font-size: 18px; }
  p.status { margin: 0 0 20px; color: #5a6675; }
  .mermaid { overflow-x: auto; }
</style>
<h1>req-0142: <!-- request title --></h1>
<p class="status"><!-- e.g. Awaiting product manager review of the functional spec --></p>
<pre class="mermaid">
flowchart LR
  intake["Intake &amp; approval"]
  definition["Product definition"]
  alignment["Technical &amp; experience alignment"]
  testready["Test readiness"]
  build["Build &amp; test"]
  deploy["Review &amp; deploy"]
  verify["Verify"]

  intake --> definition --> alignment --> testready --> build --> deploy --> verify

  class intake done
  class definition current
  class alignment,testready,build,deploy,verify pending

  classDef done fill:#15722f,stroke:#0f5a24,color:#fff
  classDef current fill:#2360a8,stroke:#1b4b85,color:#fff
  classDef pending fill:#e8ebef,stroke:#c2cbd6,color:#4a5665
</pre>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, securityLevel: "strict" });
</script>
```

Keep the seven phases and the three `classDef` colours exactly as they are, so a reader
who has seen one request's diagram can read every other one at a glance. Change only the
title, the status line and which phases are in which class.

## Updating it

Use `edit_file` on the `class` lines and the status line. Do not rewrite the file: the
diagram is versioned like every other document, and a wholesale rewrite hides the one
thing the history should show, which is when each phase actually completed.

## Naming who did what

When you write or revise a document, say in your reply which role the change was made
for ("drafted for the product owner to review"). Your own writes reach the store without
an author attached, so the sentence you write is the only account of whose turn it was.
Never claim a revision was made BY a person: they approved it, you wrote it.

## What it is not

It is your account of the request's progress, not an independent record. Never mark a
phase done because the document looks finished. Mark it done when a person approved it,
and if you are not sure whether that happened, leave it as current and ask.
