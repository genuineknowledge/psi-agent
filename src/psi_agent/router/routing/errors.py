"""Errors specific to single-target model routing."""

from ..errors import RouterError


class RouteSelectionError(RouterError):
    """The selector could not produce one configured candidate."""


__all__ = ["RouteSelectionError"]
