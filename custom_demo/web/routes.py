"""The route table: the one place that says which path reaches which handler.

Kept separate from the handlers so a subsystem module never has to know about its
neighbours, and so the contract the SPA depends on — every path, every method — can
be read in one screen.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import Route

from custom_demo.web.a2a import a2a_endpoint, agent_card
from custom_demo.web.cleanup import cleanup
from custom_demo.web.docs import docs_file, docs_files, docs_save, docs_versions
from custom_demo.web.evals import evals_run, evals_status
from custom_demo.web.feedback import feedback
from custom_demo.web.mcp import mcp_proxy
from custom_demo.web.metadata import agents, project_url, projects, tools, trace_url, workspaces
from custom_demo.web.remote_agents import remote_agent_probe, remote_task_stream
from custom_demo.web.sandbox import sandbox_file, sandbox_files, sandbox_upload
from custom_demo.web.spa import spa_routes
from custom_demo.web.traffic import demo_traffic, demo_traffic_status
from custom_demo.web.voice import voice_token, voice_trace

app = Starlette(
    routes=[
        Route("/feedback", feedback, methods=["POST"]),
        Route("/tools", tools, methods=["GET"]),
        Route("/mcp/proxy/{server_id}", mcp_proxy, methods=["POST"]),
        Route("/remote-agents/probe", remote_agent_probe, methods=["POST"]),
        Route("/remote-tasks/{task_id}/stream", remote_task_stream, methods=["GET"]),
        # Shadow the built-in A2A endpoint and card to add SubscribeToTask and push
        # notifications; every other A2A method passes through (web/a2a.py).
        Route("/a2a/{assistant_id}", a2a_endpoint, methods=["GET", "POST", "DELETE"]),
        Route("/.well-known/agent-card.json", agent_card, methods=["GET"]),
        Route("/voice/token", voice_token, methods=["POST"]),
        Route("/voice/trace", voice_trace, methods=["POST"]),
        Route("/sandbox-files", sandbox_files, methods=["GET"]),
        Route("/sandbox-file", sandbox_file, methods=["GET"]),
        Route("/sandbox-upload", sandbox_upload, methods=["POST"]),
        Route("/docs-files", docs_files, methods=["GET"]),
        Route("/docs-file", docs_file, methods=["GET"]),
        Route("/docs-file", docs_save, methods=["POST"]),
        Route("/docs-versions", docs_versions, methods=["GET"]),
        Route("/projects", projects, methods=["GET", "POST"]),
        Route("/workspaces", workspaces, methods=["GET"]),
        Route("/agents", agents, methods=["GET"]),
        Route("/cleanup", cleanup, methods=["POST"]),
        Route("/trace-url", trace_url, methods=["GET"]),
        Route("/project-url", project_url, methods=["GET"]),
        Route("/evals/run", evals_run, methods=["POST"]),
        Route("/evals/status", evals_status, methods=["GET"]),
        Route("/demo-traffic", demo_traffic, methods=["POST"]),
        Route("/demo-traffic/status", demo_traffic_status, methods=["GET"]),
        *spa_routes(),
    ]
)
