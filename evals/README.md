# Tier-3 LLM evals

Offline LangSmith evals for the behaviors the deterministic pytest suite can't
cover — the ones that depend on the model's judgment. Each group is a **dataset**
(parameterized inputs) run through a **target** and scored by **evaluators** (code
assertions + an LLM-as-judge), recorded as a LangSmith **experiment**.

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
