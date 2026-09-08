"""Custom routes mounted on Agent Server via langgraph.json `http.app`.

Agent Server serves the graph, threads, and runs. We add extra routes —
`POST /feedback` (record thumbs up/down on a run's trace), `GET /projects`
(list LangSmith tracing projects for the trace-routing picker),
`GET /sandbox-files` + `GET /sandbox-file` (browse/read the assistant's sandbox
VM for the SPA's file dialog), and `POST /evals/run` + `GET /evals/status` (kick
off and read back the assistant's per-assistant eval experiment) — so the SPA
never sees the LangSmith API key (it stays here, server-side). Served at the same
host as the deployment (`:2024` under `langgraph dev`).

One module per subsystem: `feedback`, `metadata` (workspaces/projects/agents/tools
and the URL resolvers), `mcp`, `voice`, `evals`, `sandbox` + `files`, `traffic`,
`cleanup`. `routes` assembles them into the app; `errors` holds the two error
shapes they all answer with. `custom_demo/webapp.py` re-exports `app` from
here, because `langgraph.json` names that path.
"""

from custom_demo.web.routes import app

__all__ = ["app"]
