"""Public aggregation facade for Router result handling."""

from __future__ import annotations

from .orchestrator import OrchestrationError, Orchestrator
from .prompts import build_aggregation_messages

__all__ = ["OrchestrationError", "Orchestrator", "build_aggregation_messages"]
