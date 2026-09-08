"""The route table: the one place that says which path reaches which handler.

Kept separate from the handlers so a subsystem module never has to know about its
neighbours, and so the contract the SPA depends on — every path, every method — can
be read in one screen.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import Route

from dashboard_agent.web.cleanup import cleanup
from dashboard_agent.web.evals import evals_run, evals_status
from dashboard_agent.web.feedback import feedback
from dashboard_agent.web.mcp import mcp_app, mcp_probe
from dashboard_agent.web.metadata import agents, project_url, projects, tools, trace_url, workspaces
from dashboard_agent.web.sandbox import sandbox_file, sandbox_files, sandbox_upload
from dashboard_agent.web.traffic import demo_traffic, demo_traffic_status
from dashboard_agent.web.voice import voice_token, voice_trace

app = Starlette(
    routes=[
        Route("/feedback", feedback, methods=["POST"]),
        Route("/tools", tools, methods=["GET"]),
        Route("/mcp/probe", mcp_probe, methods=["POST"]),
        Route("/mcp/app", mcp_app, methods=["POST"]),
        Route("/voice/token", voice_token, methods=["POST"]),
        Route("/voice/trace", voice_trace, methods=["POST"]),
        Route("/sandbox-files", sandbox_files, methods=["GET"]),
        Route("/sandbox-file", sandbox_file, methods=["GET"]),
        Route("/sandbox-upload", sandbox_upload, methods=["POST"]),
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
    ]
)
