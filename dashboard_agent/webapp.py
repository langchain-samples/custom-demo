"""Entry point for the custom routes: `langgraph.json` names this file's `app`.

The routes themselves live in `dashboard_agent/web/`, one module per subsystem —
see that package's docstring. This file stays because `http.app` in langgraph.json
points at it by path, and the deployment fails to start if that stops resolving.
"""

from dashboard_agent.web import app

__all__ = ["app"]
