"""Primitives with no dependencies of their own.

Anything here may be imported from anywhere in the package; nothing here imports
from the rest of it. That one-way rule is what keeps it a leaf.
"""

from dashboard_agent.core.ctx import ctx_get

__all__ = ["ctx_get"]
