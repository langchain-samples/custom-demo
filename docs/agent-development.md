# Agent development checklist (spec-first)

How we change agent *behavior* in this repo. The rule is simple: **write the spec as a failing
test before you write the code.** An agent is one irreducibly non-deterministic component; the way
we stay sane is to make everything around it deterministic and to state, up front, what "correct"
means.

## The loop

For any change to what the agent does — a new tool, a prompt edit, a backend swap, a model bump:

1. **Write the spec first.** One or more rows of:

   | When the user prompts… | our response looks like… | the world looks like… |
   |---|---|---|
   | "return a drill bought 12 days ago" | cites the returns policy code | (read-only; no state change) |
   | "file an urgent work order" | confirms it as done, once | a work order exists in the store |
   | "forecast next quarter's sales" | a chart + a stated method | a CSV was read and a script ran in the VM |

   Encode each row as a test or an eval example. The middle column is your assertions; the right
   column is what you check about state / tool-calls, not prose.

2. **Watch it fail.** A regreen you haven't seen go red is not a test — it might be asserting
   something the agent already did. Run it, see it fail for the *right* reason, then implement.

3. **Implement until green.** Change the smallest surface that makes the spec pass.

4. **Push the property to the cheapest level that can hold it.** In order of preference:
   - **Level 0 — harness (no model, free, instant):** assert on the *assembled* context/prompt and
     on middleware directly. If the prompt names a tool, assert the tool actually reaches the model.
     Worked examples: `dashboard_agent/tests/test_prompt_composition.py` (captures the composed
     system prompt via a recording stub model) and `test_agent_wiring.py` (calls middleware
     `_apply` directly). Also good here: `test_sandbox_backend.py` (backend shape with a fake VM).
   - **Level 1 — smoke:** real model, stub tools. "Did it respond / reach for the right tool."
   - **Level 2 — scripted:** feed a specific tool response (including *failures* — error, raise,
     permission-denied, empty read) and assert the agent doesn't claim false success.
   - **Level 3 — LLM-judge eval:** only for genuinely semantic properties a regex can't reach.
     Lives in `evals/` (LangSmith datasets + experiments), never in per-PR CI.

## Non-negotiables

- **Keep the app boundary out of the agent.** Data access / business rules are testable without a
  model (`datasource.py`, `tools/`); when the agent is wrong you should be able to say "the tools
  are correct" with a green run behind you.
- **Tool errors carry the fix.** An empty result reads to a model as "doesn't exist" → confident
  lies. Return an error that names the correct next step, not `{}` / `[]`.
- **Binary evaluators with a reason, and unit-test them.** A broken evaluator manufactures false
  confidence. See `evals/evaluators.py` and its tests.
- **Backends stay locked in code.** Assistant `context` never selects a filesystem/shell/sandbox
  backend — that's the security boundary in AGENTS.md §3.

## What runs where

- Per-PR CI: Level 0/1/2 (no real model, no VM, no network) — `dashboard_agent/tests/`.
- Manual / pre-release: Level 3 judge evals and live smoke tests — `evals/`, gated on env.
