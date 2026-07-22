"""Serial multi-backend routing component."""

from .entry import Router
from .server import serve_router

__all__ = ["Router", "serve_router"]
