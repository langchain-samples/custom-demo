"""Agent Server entrypoint.

`langgraph.json` points at `dashboard_agent/graph.py:graph`. Agent Server imports
this module, discovers the compiled graph, and serves it with its own persistence
(so the graph is built without a checkpointer). Demo variants are modeled as
**assistants** — configuration instances that set the `Context` fields
(prompt/dataset/model) at runtime; see scripts/seed_assistants.py.
"""

from __future__ import annotations

# Absolute import: Agent Server loads this entrypoint as a top-level module (no
# package parent), so a relative `from .agent` import would fail here.
from dashboard_agent.agent import build_graph

graph = build_graph()
