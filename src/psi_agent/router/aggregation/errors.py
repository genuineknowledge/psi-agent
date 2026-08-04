"""Errors specific to broadcast aggregation."""

from ..errors import RouterError


class AggregationError(RouterError):
    """The aggregation strategy cannot safely produce a response."""


__all__ = ["AggregationError"]
