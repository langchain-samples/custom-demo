"""Primitives with no dependencies of their own.

Anything here may be imported from anywhere in the package; nothing here imports
from the rest of it. That one-way rule is what keeps it a leaf.
"""

from custom_demo.core.ctx import Context, get_ctx

__all__ = ["Context", "get_ctx"]
