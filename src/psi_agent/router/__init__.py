"""Experimental Router framework with shared transport and pluggable strategies."""

from .aggregation import (
    AggregationConfig,
    AggregationError,
    AggregationFeedback,
    AggregationRouter,
    AggregationStrategy,
    build_aggregation_messages,
    compact_feedback,
)
from .client import RouterHttpClient
from .entry import Router
from .errors import InvalidRouterRequestError, RouterError, RouterUpstreamError
from .models import CompletionResult, RouterMode, RouterTarget
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
    "AggregationConfig",
    "AggregationError",
    "AggregationFeedback",
    "AggregationRouter",
    "AggregationStrategy",
    "CompletionResult",
    "InvalidRouterRequestError",
    "RouteSelectionError",
    "RouteSelector",
    "Router",
    "RouterError",
    "RouterHttpClient",
    "RouterMode",
    "RouterStrategy",
    "RouterTarget",
    "RouterUpstreamError",
    "RoutingConfig",
    "RoutingRouter",
    "RoutingStrategy",
    "RoutingTarget",
    "SelectionResult",
    "build_aggregation_messages",
    "build_selector_messages",
    "compact_feedback",
    "create_router_app",
    "handle_chat_completions",
    "serve_router",
]
