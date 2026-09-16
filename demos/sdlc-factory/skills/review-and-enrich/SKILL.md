---
name: review-and-enrich
description: "Use when a reviewer asks for a change to a document, quotes a passage from one, or asks whether a document is ready to pass its gate. Revises the document in place and validates it against its phase checklist."
---

# Review And Enrich

This is the step that repeats in every phase, and the one a person actually spends their
time in. Somebody is reading a document you drafted and wants it different.

## Edit the document, do not describe the edit

When the request is a change to a document, change the document:

- Use `edit_file` on the smallest passage that has to change. Rewriting the whole file to
  adjust one paragraph destroys the revision history's usefulness: the next reviewer
  cannot see what actually changed.
- Never paste a revised passage into chat for someone to copy in by hand.
- Then say, in one sentence, what you changed. The document is the answer; the sentence
  is just the receipt.

## When a passage is quoted at you

The reader can select text in a document and send it with their question. When you get a
quoted passage:

1. Find that passage in the named document.
2. Change that passage, not the section around it, and not the whole document.
3. If the request would contradict something elsewhere in the document, make the edit and
   say which other section now disagrees. Do not silently fix both, and do not refuse.

"Tighten this up", "this is too vague", "say it in their words" are all edits to that
passage. Make them.

## How hard to review

`request.md` records this request's review level, and it decides how you answer "is this
ready?":

| Review | What you do |
|---|---|
| `adversarial` | Hunt for contradictions, missing cases and unstated assumptions. Report them as blocking, and say the gate should not pass until they are answered. |
| `advisory` | One pass. Report what you find. Say plainly that none of it blocks the gate. |
| `none` | Do not review unasked. If someone asks directly, answer their question and nothing more. |

The effective level is the LOWEST of what the stage declares, what the profile caps, and
what the person asked for on this request. So a team on `express` gets no reviews even at
a stage that would normally run one, and asking for an advisory pass on an `enterprise`
request lowers that one request rather than the profile.

Every finding names the section it came from. A finding a reader cannot locate is an
opinion, and the point of a review is that someone can go and look.

## Validate before a gate

When someone asks whether a document is ready, check it against its phase and answer
with what is actually wrong, not with reassurance:

**Intake brief:** does it say who has the problem, what changes afterwards, and how they
would know it worked? Is it in the requester's words? Is it a page or less?

**Functional spec:** does every acceptance criterion describe one observable outcome? Is
every assumption marked as an assumption? Does anything contradict the brief? Are the
out-of-scope items the ones a reader would otherwise assume?

**BDD features:** does every acceptance criterion have at least one scenario? Does every
scenario have exactly one `When`? Are the refusal and limit cases there? Does any step
name a UI mechanic instead of a behaviour?

Report it as a short list of what to fix, each item naming the section. If nothing is
wrong, say so plainly and ask for the approval.

## Never approve on someone's behalf

You validate; a person approves. Writing "approved" into a document because the checklist
passed forges a decision that was never made, and the whole process exists to record
those decisions.

So mark a gate `[x]` in `request.md` only once a person has actually approved it, and
record who, in their words. Until then it stays `[>]`, however finished the document
looks. Then regenerate the diagram (read `progress-diagram`).
