"""Errors specific to serial fallback routing."""

from ..errors import RouterError


class FallbackError(RouterError):
    """Every eligible fallback candidate failed to produce a usable response."""


__all__ = ["FallbackError"]
