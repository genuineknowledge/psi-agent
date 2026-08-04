"""Errors shared by every experimental Router strategy."""


class RouterError(Exception):
    """Base class for errors that can be reported at the Router boundary."""


class InvalidRouterRequestError(RouterError):
    """The caller supplied a malformed Chat Completions request."""


class RouterUpstreamError(RouterError):
    """An upstream response cannot be used safely."""


__all__ = [
    "InvalidRouterRequestError",
    "RouterError",
    "RouterUpstreamError",
]
