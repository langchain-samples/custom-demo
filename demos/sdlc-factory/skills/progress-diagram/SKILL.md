---
name: progress-diagram
description: "Use after any stage completes or a gate is passed, or when someone asks how far along a request is. Renders the progress diagram for a request folder from its request record."
---

# Progress Diagram

Every request folder carries a `progress.html` that draws the stages this request runs
and marks where it has got to. It opens as a tab beside the documents, so "how far along
is this?" is answered by looking.

## Render it, do not maintain it

`request.md` is the state. This diagram is a PICTURE of that state, generated from it. So
the order is always: update `request.md` first, then read it, then regenerate the diagram
to match.

Never edit the diagram to record progress, and never let it carry a stage the record does
not. Two places holding the same state means one of them is wrong and nobody can tell
which, and the one people look at is this one.

## What it shows

Only the stages this request's profile runs, using the record's own checkboxes:

| Record | Diagram class | Meaning |
|---|---|---|
| `[x]` | `done` | Finished, and for a gate, approved by a person |
| `[>]` | `current` | Being worked on now, including waiting at a gate |
| `[ ]` | `pending` | Not started |
| `[-]` | `skipped` | A conditional stage whose condition did not hold |

A gate is only `done` when a person approved it. A draft sitting at a gate is `current`.

## The file

Write `/workspace/artifacts/<request-id>/progress.html`. Keep the structure and the four
colours exactly as below, so a reader who has seen one request's diagram can read every
other one at a glance. Change only the title, the status line, the profile line, the node
list and the classes.

```html
<!doctype html>
<meta charset="utf-8">
<title>req-0142 progress</title>
<style>
  body { margin: 0; padding: 24px; font: 14px system-ui, sans-serif; color: #16202c; background: #fff; }
  h1 { margin: 0 0 4px; font-size: 18px; }
  p { margin: 0 0 6px; color: #5a6675; }
  p.profile { font: 12px ui-monospace, monospace; color: #7b8796; margin-bottom: 20px; }
  /* The diagram keeps its natural size and this scrolls, rather than the diagram being
     squeezed to the pane width. A twelve-stage row scaled to fit is unreadable. */
  .mermaid { overflow-x: auto; }
</style>
<h1>req-0142: Gift with purchase at checkout</h1>
<p>Awaiting product manager review of the functional spec</p>
<p class="profile">profile: change &middot; depth: standard &middot; review: advisory &middot; 9 stages &middot; 2 gates</p>
<pre class="mermaid">
flowchart TD
  subgraph P0[" Initialization "]
    direction LR
    s01["0.1 Request Intake"] --> s02["0.2 Practices Discovery"]
  end
  subgraph P1[" Ideation "]
    direction LR
    s11["1.1 Intent Capture"] --> s13{{"1.3 Approval &amp; Handoff"}}
  end
  subgraph P2[" Inception "]
    direction LR
    s21["2.1 Reverse Engineering"] --> s22["2.2 Requirements Analysis"]
    s22 --> s23["2.3 NFR Requirements"] --> s25["2.5 Acceptance Criteria"]
    s25 --> s27{{"2.7 Definition Ready"}}
  end

  s02 --> s11
  s13 --> s21

  class s01,s02,s11,s13 done
  class s22 current
  class s21 skipped
  class s23,s25,s27 pending

  classDef done fill:#15722f,stroke:#0f5a24,color:#fff
  classDef current fill:#2360a8,stroke:#1b4b85,color:#fff
  classDef pending fill:#eef1f5,stroke:#c2cbd6,color:#3d4855
  classDef skipped fill:#f6f7f9,stroke:#c8d0da,color:#8b95a2,stroke-dasharray:4 3
</pre>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({
    startOnLoad: true,
    securityLevel: "strict",
    // Without this mermaid caps the SVG at the container width, which shrinks a long
    // process until nobody can read a single stage name.
    flowchart: { useMaxWidth: false, nodeSpacing: 28, rankSpacing: 44 },
    themeVariables: { fontSize: "15px" },
  });
</script>
```

Two shapes carry meaning: `{{...}}` is a gate, `[...]` is an ordinary stage. Node ids are
the stage number with the dot removed, so `2.5` is `s25`. A skipped stage stays in the
chain, dashed and grey: the reader should see that the process considered it and why, not
find a gap.

**One phase per row.** The stages of a phase run left to right inside its own subgraph, and
the phases stack downwards. Join the phases NODE to node (`s02 --> s11`), never subgraph
to subgraph (`P0 --> P1`): mermaid ignores a subgraph's own `direction` once the subgraph
itself is an endpoint, and the rows collapse back into one long line. Never put the whole process on one line: twelve stages in a
single row is about 2000 pixels wide, and a reader who has to scroll sideways past every
finished stage to find the current one would rather you had written a sentence. Include
only the stages this profile runs, so a `bugfix` request draws five nodes and its rows are
shorter still.

## Updating it

Use `edit_file` on the status line, the profile line and the `class` lines. Do not rewrite
the file: the diagram is versioned like every other document, and a wholesale rewrite
hides the one thing the history should show, which is when each stage actually completed.

When a profile or depth changes at a gate, the node list changes too. That is a real edit
to the chain, not just to the classes.

## What it is not

It is the request record's account of progress, not an independent audit. It is only as
true as the record, so never mark a stage done here to make the picture look finished.
