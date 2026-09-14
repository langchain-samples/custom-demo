# Custom Demo Agent

A **deep agent** that answers a question by building a live dashboard: it reads the data it
has, emits validated widget specs (KPI cards, charts, tables, key findings), and a React SPA
renders each one the moment its tool call finishes streaming.

It is a **customer-demo platform**. One shared graph, one parameterized frontend, and a
per-customer **assistant** carrying that customer's branding, prompt, data and capabilities.
Setting up a new demo is a form, not a fork.

📹 **[Watch the walkthrough](https://www.loom.com/share/d5ce4bb5a2b5485baef75d0a1d84f825)** (Loom)

## Run it

You need **uv**, **Node 20+**, a **LangSmith key** and a **model provider key** (Anthropic by
default; any `init_chat_model` provider works).

```bash
uv sync --group dev
cp .env.example .env                    # then edit: LANGSMITH_API_KEY + a model key
uv run python scripts/preflight.py      # names what is missing, and the fix
uv run ./run.sh                         # Agent Server :2024 + the SPA :3000
```

Open <http://127.0.0.1:3000>, and in ⚙️ pick a **Workspace**, hit **+ New** to set up a
customer assistant, and ask it something.

Run `preflight.py` first. It makes one cheap model call and one LangSmith round trip, then
prints `ALL CHECKS PASSED` or the specific fix. Most setup problems surface there rather than
as a stack trace ten minutes into a demo.

**Python 3.13 exactly, not "3.13 or newer".** It is the newest Python the LangGraph deployment
base image builds, so `.python-version`, `requires-python` and CI all hold that line; otherwise
3.14-only behaviour passes CI and fails the deploy. If your `.venv` was built on 3.14, `uv sync`
will refuse it:

```bash
rm -rf .venv && uv sync --group dev
```

## The planted bug, and fixing it live

Each assistant is created with a **failure mode**. With `hallucination`, the system prompt tells
the agent to answer confidently over gaps, and setup plants one: a topic the seeded files
genuinely do not cover, plus a quick action that asks about it. The arc:

1. Two grounded questions get real, checkable answers.
2. The third, the gap probe, gets a confident fabrication.
3. Open the assistant's prompt in **LangSmith Context Hub** (its `<slug>-agent` repo,
   `AGENTS.md`), delete the fabricate-over-gaps clause, save. The next question is honest,
   with **no restart**: the prompt is pulled per turn.
4. **Evals** in the header scores the arc against that assistant's own dataset. 2/3 before the
   fix, 3/3 after.

The gap is a real absence rather than an instruction to withhold, which is what makes step 2
reliable: the agent has nowhere to read the figure from, so stating it is a genuine
hallucination.

## Tests

```bash
uv run pytest custom_demo/tests evals -q                      # the whole suite, ~7s
uv run ruff check custom_demo scripts evals mcp_demo_server   # + ruff format --check, ty check
cd frontend && npx tsc -b && npx oxlint src && npx vitest run && npx vite build
```

`.github/workflows/ci.yml` is the full list, and every step in it is a gate. Live-LLM tests skip
themselves without `ANTHROPIC_API_KEY`, which CI leaves unset on purpose so nothing there makes
a real API call.

## Where to read next

- **[AGENTS.md](AGENTS.md)** - what exists and where to add it: repo map, runtime architecture,
  extension points, rough edges. Read its section 6 first.
- **[CLAUDE.md](CLAUDE.md)** - the conventions, and the judgment the checks cannot make.
- **[docs/mcp-apps-with-deep-agents.html](docs/mcp-apps-with-deep-agents.html)** - MCP Apps
  (SEP-1865): how a tool ships its own UI, and what a host has to do to render one.
- **[custom_demo/config.py](custom_demo/config.py)** - every environment variable, each with the
  reason it exists.
