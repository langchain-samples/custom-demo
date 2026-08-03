# Tier-3 LLM evals

Offline LangSmith evals for the behaviors the deterministic pytest suite can't
cover — the ones that depend on the model's judgment. Each group is a **dataset**
(parameterized inputs) run through a **target** and scored by **evaluators** (code
assertions + an LLM-as-judge), recorded as a LangSmith **experiment**.

> **Not the per-assistant demo eval — and the polarity is inverted.**
>
> `dashboard_agent/assistant_evals.py` is a *different* eval system that ships as part of
> the product: setup creates a 3-example dataset in each customer's own workspace, and the
> presenter re-runs it from a button in the SPA.
>
> | | here (`evals/`) | `dashboard_agent/assistant_evals.py` |
> |---|---|---|
> | purpose | regression-test **our repo** before a release | a live artifact **of the demo** |
> | dataset | `dashboard-agent-*` in `EVAL_WORKSPACE` | `<customer-slug>-demo-evals-<fingerprint>`, in the customer's workspace |
> | **score 1** | the planted **bug fired** (`_fabricates` → the agent invented figures) | the agent was **correct** (admitted the gap / hedged, no fabricated figures) |
> | when | manual, gated on env, never in per-PR CI | at assistant creation, then on demand mid-demo |
>
> The demo reads **2/3 (red) → fix the prompt in Prompt Hub → 3/3 (green)**, which only works
> because that evaluator rewards correct behavior. Copy an evaluator across this line without
> flipping it and the demo scores green before the fix. See AGENTS.md §3.
>
> **Layering:** `evals/` may import from `dashboard_agent` (it does — `fixtures.py` reuses the
> real setup helpers). `dashboard_agent` must never import from `evals/`; that is why the demo
> evaluator and its judge helper live in `assistant_evals.py`.

| group | dataset | target | checks |
|---|---|---|---|
| `setup` | `dashboard-agent-setup` | `analyze_customer` | quick actions are specific + on-scenario (judge) |
| `data`  | `dashboard-agent-data`  | synthetic data source | gap topic → 0 rows; non-gap → rows |
| `agent` | `dashboard-agent-agent` | the deep agent (Context Hub) | skill read before datasearch + marker cited; hallucination fabricates; file written |

## Run

```bash
export ANTHROPIC_API_KEY=...            # real model (targets + judge)
export EVAL_WORKSPACE=<workspace-id>    # LangSmith tenant for the fixtures + datasets
export LANGSMITH_API_KEY=<dataset-capable key>
export LANGSMITH_WORKSPACE_ID=<workspace-id>   # else /datasets 403s in the key's default tenant

uv run python -m evals.run                # all three groups
uv run python -m evals.run --only data    # cheapest; no agent, no fixtures
uv run python -m evals.run --only setup
uv run python -m evals.run --only agent   # pushes the `evals-agent` Context Hub fixture first
```

The command prints each experiment; open it in the LangSmith UI to see per-example
scores and comments, and to compare runs over time (e.g. after a prompt change).

## Notes

- Gated: with `ANTHROPIC_API_KEY`/`EVAL_WORKSPACE` unset it prints setup help and exits 0.
  Never run in per-PR CI (real model + writes to a workspace).
- `EVAL_WORKSPACE` falls back to `CTXHUB_TEST_WORKSPACE` (same fixture workspace the e2e test uses).
- The `evals-agent` repo + its skills are fixed and re-pushed idempotently each run (like the
  e2e-test fixtures); delete them from Context Hub if you want a clean slate.
- Datasets are created once and then left alone, so experiment history stays comparable. Rename
  a dataset (or delete it in the UI) to reseed with new examples.
- These datasets are ours and are never deleted by the app. The per-assistant demo datasets are
  the opposite: they are recorded in the assistant's `metadata.ls_artifacts.eval_dataset` and
  cascade-deleted by `POST /cleanup` when the assistant is deleted.
