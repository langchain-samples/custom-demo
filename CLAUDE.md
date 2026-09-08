# CLAUDE.md

Conventions for writing code in this repo. One home per rule: this file holds what the
checks fail on, the judgment they cannot make, and the names that must not change.
**[AGENTS.md](AGENTS.md)** holds what exists and where to add it (repo map, runtime
architecture, extension points, rough edges); read its section 6 first. That root
`AGENTS.md` is a developer-facing orientation doc and is **not** anyone's system prompt:
an assistant's system prompt is the `AGENTS.md` inside its *Context Hub agent repo* (see
`runtime/prompt.py`), a different file that happens to share the name. Do not edit one
thinking you are editing the other. Python is 3.13 everywhere on purpose, `uv run`
everything.

## What the checks already enforce

Do not restate any of these anywhere. Run the check instead.

`uv run ruff check dashboard_agent scripts evals mcp_demo_server`, with
`select = ["E","W","F","I","UP","B","C4","D","PLC0415","TID252","BLE001","RUF100"]`
and `E501` ignored:

- `D` on the Google convention: a docstring on every module, class and function
  outside tests, which relax only `B011` and `D100-D104`.
- `PLC0415` keeps imports at the top of the file; `TID252` with
  `ban-relative-imports = "all"` keeps them absolute. Both waivable with a `# noqa`
  plus a stated reason.
- `BLE001`: a bare `except Exception` needs `# noqa: BLE001 - <reason>`. `RUF100`: an
  unused noqa is an error, so a stale waiver cannot survive.

The rest of `.github/workflows/ci.yml`, where every step is a gate:

- `ruff format --check` and `ty check`, same four paths.
- `uv run python scripts/check_blank_after_block.py`: once an indented block ends, the
  next statement at that indentation needs a blank line above it. `--fix` inserts them.
- Import smoke on the four entrypoints `langgraph.json` names.
- `uv run pytest dashboard_agent/tests evals -q`.
- Frontend: `oxlint` (`react/rules-of-hooks`, `react-hooks/exhaustive-deps` and
  `react/iframe-missing-sandbox` are errors), `vitest`, `tsc -b && vite build`, and
  every `dashboard_agent/tests/*.js` under plain node from the repo root.
- No em-dashes. `frontend/scripts/check-no-emdash.mjs` scans `frontend/src` (code
  comments exempt), `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/` and `evals/*.md`.
  `test_prompt_prose.py` scans Python string literals in the five modules whose text
  reaches a model or a user. Use commas, colons, parentheses, or " - ".

Conventions pinned by a contract test. Change the test if you mean to change the rule:

- `test_cleanup_contract.py`: the `ls_artifacts` key set agrees across
  `provisioning/setup.py`, `web/cleanup.py` and `frontend/src/lib/api.ts`.
- `test_sandbox_target_contract.py`: every SPA literal setting `agent_repo` carries
  `sandbox_key` beside it.
- `test_tool_vocabulary.py`: every catalogue tool appears in every SPA map keyed by
  tool name.
- `test_demo_prompt_contract.py`: one behavioural clause per prompt, and the README
  sends the presenter where the prompt actually lives.
- `test_agent_wiring.py`: the deployed agent has no `write_todos`.
- `test_blank_after_block.py`: the blank-line checker's own false-positive cases.

## Judgment the checks cannot make

### Fail loudly, and never substitute content

AI-written code is afraid of exceptions, and the result is an agent that is quietly
weird instead of a UI that says "that did not work, try again." Prefer the exception.

`runtime/prompt.py` is the worked example. Returning `FALLBACK_PROMPT` on any exception
makes a Hub outage, a typo'd repo handle and a missing permission indistinguishable from
"this customer has no prompt", and the run then answers as a generic assistant wearing
the customer's name. So `pull_agent_prompt` raises a typed `PromptSourceError` naming the
repo and chaining the cause, and its caller propagates it so the SPA renders the
sentence. Copy that shape: a named error type, a message for the human,
`raise ... from exc`. Two sharpenings decide the hard cases.

**An explicitly requested capability failing silently is indefensible.** If an operator
set an env flag or a user typed a value, they asked for it. A default quietly not
applying is sometimes fine, because nothing was asked for and there is nothing to fail
about: an assistant with no `agent_repo` correctly gets `FALLBACK_PROMPT`, applied
directly in `agent.py` without ever reaching the Hub.

**Never substitute unrelated data.** A medical-records assistant was once seeded with a
retail sales CSV and reasoned over it as if it were its own. The test is: if this
fallback fires, will someone act on wrong information believing it is right? If yes it is
not a fallback, it is a fabrication. Where a failure genuinely is best-effort (a
brand-colour scrape, one bad skill in a setup), say which thing failed and why on the way
past. The silence is the defect, not the degradation.

**Decide control flow by exception type, not by substring.** Five sites once concluded a
push had already succeeded by testing `"409" in msg or "conflict" in msg`, so a request
id containing 409 handed back a handle for a repo that was never written.
`provisioning/setup.py:_already_committed` is the fixed shape: `isinstance(exc,
LangSmithConflictError)`. If you must match text, say in a comment that it is a
wire-format dependency and pin it in a test.

### Comments must read to someone who never saw the old version

"This used to be X", "now uses Y instead", "still defaults to Z", "no longer needed":
all broken for a cold reader, who has no X and no before. Guardrails are welcome, but
phrase them forward and keep the specifics. Not "no longer seeds here" but "Don't seed
on the attach path: its `pip install` blocks the turn inside a middleware with no timeout."

### Shape

- **No fat nestings.** An `if:` followed by a screen of indented lines is the smell.
  Guard clauses, early returns, extracted siblings. `web/sandbox.py`'s `sandbox_file`
  was a 143-line body inside one `try:`; it is 34 lines with a 4-line largest block,
  once the `try` became `web/errors.py`'s `@route_error` decorator and the arms became
  `_media_page`, `_text_page` and `_placeholder`.
- **Pydantic models, not `list[dict]`.** `runtime/widgets.py` is the reference;
  `provisioning/traffic.py:seed_questions(actions: list[dict] | None) -> list[dict]` is
  the outstanding counterexample. Do not add another.
- **Dot notation where the attribute is declared on the class.** Keep `getattr` only
  where the shape genuinely varies, and say which reason applies. `core/ctx.py` is the
  worked example: `Context` is a pydantic `BaseModel` there and `get_ctx(runtime)`
  returns it, so every call site reads a declared attribute and `ty` rejects a typo that
  a `getattr` default would have answered with `None`. Validation at the run boundary is
  also what makes an `isinstance` check at the call site dead rather than merely hidden:
  under a dataclass `context_schema` LangGraph validates nothing, so the annotation is
  unenforced and the check is load-bearing. The one surviving `getattr` there reads
  `.context` off the runtime object, whose shape does vary.
- **Delete, do not tidy.** When something is unused, remove it. `run` and `run_stream`
  were a server-side second implementation of `ChatPanel`'s streaming, and deleting 203
  lines also removed the four worst-nested functions in the file. Grep for callers
  across Python, TypeScript, Markdown, JSON, shell, TOML and YAML first, including the
  `getattr` and `import` forms.

### Measure, do not infer

This is the failure mode with the worst record here, and it is specifically an agent
failure mode. Confident claims that were wrong until measured: `streamEvent.ts` was
taken for the TypeScript twin of a Python streaming function and shares no behaviour
with it (the real counterpart is `onStreamMessage` in `ChatPanel.tsx`); an import was
assumed free, and hoisting `fastmcp` cost graph cold start 466ms to about 880ms; a hang
was pinned on the wrong cause until the trace showed the run pending in
`SkillsMiddleware.before_agent`. A statement about runtime behaviour arrives with the
command or trace that produced it, or it is a guess and says so. The same applies to
claiming a capability exists: check it is installed first. A prompt once advertised
`write_todos`, which deepagents 0.7 does not enable.

### Never `git checkout` or `git restore` a file holding uncommitted work

It destroyed work twice in one session. To undo a deliberate experiment, revert the
exact edit you made. The same goes for `git stash` and `git reset` on a dirty tree.

## Names that must not change

Renaming any of these breaks state that already exists on a server or in a browser.

- **`dashboard_agent`, the graph KEY in `langgraph.json`.** Every existing assistant is
  bound to that `graph_id`, and `frontend/src/lib/config.ts:GRAPH_ID` repeats it. The
  Python package may be renamed. The graph key may not.
- **`dashboardWorkspace`**, the localStorage key in `SettingsPanel.tsx`. Renaming it
  makes every user re-pick their workspace.
- **`dashboard-agent-*` LangSmith dataset names** and the **`da-`** experiment prefix
  beside them, both in `evals/run.py`. They exist server-side already.

The bare word "dashboard" is legitimate in most of its remaining uses: the product
really does build dashboards, `/skills/dashboard/SKILL.md` is a live skill path the
prompt names, and `DashboardCanvas.tsx` holds a `getElementById("dashboard")` DOM
contract. Only the product-wide naming was retired. Do not sweep the word.
