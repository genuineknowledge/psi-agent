"""Experimental Router framework with shared transport and pluggable strategies."""

from psi_agent._router_status import RouterStatus, router_status_from_event

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
from .fallback import FallbackConfig, FallbackError, FallbackRouter, FallbackStrategy
from .models import (
    BufferedCompletion,
    CompletionResult,
    RouterBackendType,
    RouterMode,
    RouterTarget,
    RouterUpstream,
    RoutingScopeKey,
)
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
    "BufferedCompletion",
    "CompletionResult",
    "FallbackConfig",
    "FallbackError",
    "FallbackRouter",
    "FallbackStrategy",
    "InvalidRouterRequestError",
    "RouteSelectionError",
    "RouteSelector",
    "Router",
    "RouterBackendType",
    "RouterError",
    "RouterHttpClient",
    "RouterMode",
    "RouterStatus",
    "RouterStrategy",
    "RouterTarget",
    "RouterUpstream",
    "RouterUpstreamError",
    "RoutingConfig",
    "RoutingRouter",
    "RoutingScopeKey",
    "RoutingStrategy",
    "RoutingTarget",
    "SelectionResult",
    "build_aggregation_messages",
    "build_selector_messages",
    "compact_feedback",
    "create_router_app",
    "handle_chat_completions",
    "router_status_from_event",
    "serve_router",
]
