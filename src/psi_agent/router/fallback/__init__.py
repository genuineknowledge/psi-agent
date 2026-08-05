"""Serial fallback configuration, errors, strategy, and runtime entry."""

from .entry import FallbackRouter
from .errors import FallbackError
from .models import FallbackConfig
from .strategy import FallbackStrategy

__all__ = ["FallbackConfig", "FallbackError", "FallbackRouter", "FallbackStrategy"]
