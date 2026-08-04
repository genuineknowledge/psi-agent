"""Experimental Router framework with shared transport and pluggable strategies."""

from .client import RouterHttpClient
from .errors import InvalidRouterRequestError, RouterError, RouterUpstreamError
from .models import CompletionResult
from .routing import (
    RouteSelectionError,
    RouteSelector,
    RoutingConfig,
    RoutingRouter,
    RoutingStrategy,
    RoutingTarget,
    SelectionResult,
    build_selector_messages,
)
from .server import RouterStrategy, create_router_app, handle_chat_completions, serve_router

__all__ = [
    "CompletionResult",
    "InvalidRouterRequestError",
    "RouteSelectionError",
    "RouteSelector",
    "RouterError",
    "RouterHttpClient",
    "RouterStrategy",
    "RouterUpstreamError",
    "RoutingConfig",
    "RoutingRouter",
    "RoutingStrategy",
    "RoutingTarget",
    "SelectionResult",
    "build_selector_messages",
    "create_router_app",
    "handle_chat_completions",
    "serve_router",
]
